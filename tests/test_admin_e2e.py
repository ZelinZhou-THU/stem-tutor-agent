from __future__ import annotations

import json
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import nest_asyncio
nest_asyncio.apply()

from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://localhost:8000"


def _login_admin(page):
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.setItem('stem_tutor_onboarding_done', '1')")
    auth_screen = page.locator(".auth-screen")
    if auth_screen.is_visible():
        page.locator(".auth-tab", has_text="登录").click()
        page.locator('#auth-username').fill("admin")
        page.locator('#auth-password').fill("admin123")
        page.locator("#auth-login-form .auth-submit").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)


def _create_test_user_and_data():
    import time
    from web.database import _ensure_db, create_user, save_run, save_chat, save_report, save_mastery, get_db

    ts = str(int(time.time() * 1000))

    async def _setup():
        await _ensure_db()
        uname = "e2e_user_" + ts
        uid = await create_user(uname, "fakehash")
        rid = "e2e-run-" + ts
        await save_run(rid, uid, {
            "run_meta": {"run_id": rid, "subject_id": "calculus"},
            "user_status": "complete",
            "raw_output": {
                "problem_input": {"problem_text": "求 x^2 的导数"},
                "normalized_steps": [
                    {"step_id": 1, "raw_text": "d/dx(x^2) = 2x", "label": "correct", "confidence": 0.95}
                ],
            },
        }, status="success", subject="calculus", problem_text="求 x^2 的导数")
        await save_chat(rid, uid, [
            {"role": "user", "content": "请帮我分析这道题"},
            {"role": "assistant", "content": "这是一道基础求导题"},
        ])
        await save_report("e2e-rep-" + ts, uid, {"title": "E2E测试报告", "content": "报告内容"})
        await save_mastery(uid, {"errors": {"calculus": 2}, "practice_history": [{"topic": "derivatives"}]})
        return uid, uname

    return asyncio.get_event_loop().run_until_complete(_setup())


def test_admin_user_detail_e2e():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        _login_admin(page)
        uid, uname = _create_test_user_and_data()

        page.locator('[data-page="admin"]').click()
        page.wait_for_timeout(2000)
        page.wait_for_load_state("networkidle")

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step1_list.png", full_page=True)

        admin_content = page.locator("#admin-content")
        expect(admin_content).to_be_visible(timeout=15000)

        view_btn = page.locator("tr").filter(has_text=uname).locator(".admin-view-btn")
        expect(view_btn).to_be_visible(timeout=5000)
        view_btn.click()

        page.wait_for_timeout(2000)
        page.wait_for_load_state("networkidle")

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step2_detail.png", full_page=True)

        detail_view = page.locator("#admin-user-detail")
        expect(detail_view).to_be_visible()

        hash_value = page.evaluate("location.hash")
        assert hash_value.startswith("#admin/user/"), f"Expected hash #admin/user/..., got {hash_value}"

        username_el = page.locator("#admin-detail-username")
        expect(username_el).to_contain_text(uname + " -")

        info_el = page.locator("#admin-detail-info")
        expect(info_el).to_be_visible()

        runs_panel = page.locator("#admin-detail-runs")
        expect(runs_panel).to_be_visible()
        run_cards = page.locator(".admin-run-card")
        assert run_cards.count() >= 1, "Should have at least one run card"

        first_run = run_cards.first
        first_run.click()
        page.wait_for_timeout(1500)

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step3_run_modal.png", full_page=True)

        modal = page.locator("#admin-run-detail-modal")
        expect(modal).to_be_visible()
        detail_body = page.locator("#admin-run-detail-body")
        expect(detail_body).to_contain_text("求 x^2 的导数")

        close_btn = page.locator("#admin-run-detail-close")
        close_btn.click()
        expect(modal).to_be_hidden()

        reports_tab = page.locator(".admin-detail-tab", has_text="学习报告")
        reports_tab.click()
        page.wait_for_timeout(500)

        reports_panel = page.locator("#admin-detail-reports")
        expect(reports_panel).to_have_class(re.compile(r"\bactive\b"))
        expect(reports_panel).to_be_visible()

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step4_reports.png", full_page=True)

        report_cards = page.locator("#admin-reports-list .admin-report-card")
        assert report_cards.count() >= 1, "Should have at least one report"

        chats_tab = page.locator(".admin-detail-tab", has_text="聊天记录")
        chats_tab.click()
        page.wait_for_timeout(500)

        chats_panel = page.locator("#admin-detail-chats")
        expect(chats_panel).to_have_class(re.compile(r"\bactive\b"))
        expect(chats_panel).to_be_visible()

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step5_chats.png", full_page=True)

        chat_cards = page.locator("#admin-chats-list .admin-chat-card")
        assert chat_cards.count() >= 1, "Should have at least one chat"

        settings_tab = page.locator(".admin-detail-tab", has_text="设置与掌握度")
        settings_tab.click()
        page.wait_for_timeout(500)

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step6_settings.png", full_page=True)

        settings_panel = page.locator("#admin-detail-settings")
        expect(settings_panel).to_have_class(re.compile(r"\bactive\b"))
        expect(settings_panel).to_be_visible()

        back_btn = page.locator("#admin-detail-back")
        back_btn.click()
        page.wait_for_timeout(500)

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step7_back.png", full_page=True)

        list_view = page.locator("#admin-user-list")
        expect(list_view).to_be_visible()
        detail_view_back = page.locator("#admin-user-detail")
        expect(detail_view_back).to_be_hidden()

        hash_after_back = page.evaluate("location.hash")
        assert hash_after_back == "#admin", f"Expected #admin, got {hash_after_back}"

        page.goto(BASE_URL + "#admin/user/" + str(uid))
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step8_refresh.png", full_page=True)

        detail_view_refresh = page.locator("#admin-user-detail")
        expect(detail_view_refresh).to_be_visible()
        username_refresh = page.locator("#admin-detail-username")
        expect(username_refresh).to_contain_text(uname + " -")

        browser.close()
        print("E2E test passed!")


def test_admin_non_admin_blocked():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")

        auth_screen = page.locator(".auth-screen")
        if auth_screen.is_visible():
            page.locator(".auth-tab", has_text="登录").click()
            page.locator('#auth-username').fill("e2e_testuser")
            page.locator('#auth-password').fill("fakehash")
            page.locator("#auth-login-form .auth-submit").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)

        page.goto(BASE_URL + "#admin")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        page.screenshot(path="C:/Users/17785/AppData/Local/Temp/opencode/admin_step9_nonadmin.png", full_page=True)

        admin_nav = page.locator('[data-page="admin"]')
        assert not admin_nav.is_visible(), "Admin nav should be hidden for non-admin users"

        browser.close()
        print("Non-admin block test passed!")


if __name__ == "__main__":
    import re
    test_admin_user_detail_e2e()
    test_admin_non_admin_blocked()
    print("\nAll E2E tests passed!")
