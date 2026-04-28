"""Tests for topic inference and review problem prompt."""
from __future__ import annotations

import pytest

from stem_tutor.nodes.generate_review_problems import _infer_topic_tags
from stem_tutor.prompts.templates import review_problem_prompt


class TestInferTopicTags:
    def test_ode_detection(self):
        tags = _infer_topic_tags(
            "求解常微分方程：cos x cos y dy - sin x sin y dx = 0",
            "分离变量后得到 cot y dy = tan x dx"
        )
        assert "常微分方程" in tags

    def test_integral_detection(self):
        tags = _infer_topic_tags(
            "利用Euler积分计算下列积分",
            "Beta函数与Gamma函数的关系"
        )
        assert "积分计算" in tags

    def test_limit_detection(self):
        tags = _infer_topic_tags(
            "计算极限 lim x->0 sin(x)/x",
            "洛必达法则"
        )
        assert "极限" in tags

    def test_derivative_detection(self):
        tags = _infer_topic_tags(
            "求导数 f'(x)",
            "链式法则求导"
        )
        assert "导数与微分" in tags

    def test_series_detection(self):
        tags = _infer_topic_tags(
            "判断级数收敛性",
            "泰勒展开"
        )
        assert "级数" in tags

    def test_multivariable_detection(self):
        tags = _infer_topic_tags(
            "计算偏导数和重积分",
            "梯度"
        )
        assert "多元微积分" in tags

    def test_no_match(self):
        tags = _infer_topic_tags("hello world", "nothing related")
        assert tags == []

    def test_max_three_tags(self):
        text = "微分方程 积分 极限 导数 级数 偏导数"
        tags = _infer_topic_tags(text, text)
        assert len(tags) <= 3


class TestReviewProblemPrompt:
    def test_all_correct_prompt(self):
        prompt = review_problem_prompt([], ["常微分方程"], all_correct=True)
        assert "优秀" in prompt
        assert "进阶" in prompt
        assert "常微分方程" in prompt

    def test_error_prompt(self):
        prompt = review_problem_prompt(["OBJECT_CONFUSION"], ["积分计算"], all_correct=False)
        assert "薄弱知识点" in prompt
        assert "OBJECT_CONFUSION" in prompt

    def test_all_correct_fallback_default_topic(self):
        prompt = review_problem_prompt([], [], all_correct=True)
        assert "微积分" in prompt

    def test_math_format_hint_in_both(self):
        p1 = review_problem_prompt([], ["test"], all_correct=True)
        p2 = review_problem_prompt(["err"], ["test"], all_correct=False)
        assert "$" in p1
        assert "$" in p2
