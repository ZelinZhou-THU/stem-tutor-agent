(function () {
    "use strict";

    var SITE_TITLE = "\u6570\u7406\u57fa\u7840\u8bfe\u7a0b\u5b66\u4e60\u8f85\u5bfc\u7cfb\u7edf";
    var _lastResult = null;
    var cropperInstance = null;
    var cropCallback = null;

    var LABEL_MAP = {
        correct: { text: "\u6b63\u786e", cls: "label-correct" },
        incorrect_math: { text: "\u6570\u5b66\u9519\u8bef", cls: "label-incorrect" },
        inconsistent_or_unsupported: { text: "\u65e0\u4f9d\u636e", cls: "label-unsupported" },
        unclear: { text: "\u4e0d\u6e05\u6670", cls: "label-unclear" },
        unverified: { text: "\u672a\u9a8c\u8bc1", cls: "label-unclear" },
    };

    var DIFFICULTY_MAP = { easy: "\u7b80\u5355", medium: "\u4e2d\u7b49", hard: "\u56f0\u96be" };

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function formatDuration(seconds) {
        if (seconds == null) return "--";
        var m = Math.floor(seconds / 60);
        var s = seconds % 60;
        if (m > 0) return m + "\u5206" + s + "\u79d2";
        return s + "\u79d2";
    }

    function formatTimestamp(ts) {
        if (!ts) return "--";
        try {
            var d = new Date(ts);
            return new Intl.DateTimeFormat("zh-CN", {
                timeZone: "Asia/Shanghai",
                year: "numeric",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            }).format(d).replace(/\//g, "-");
        } catch (e) {
            return ts.substring(0, 16);
        }
    }

    function normalizeRunStatus(status) {
        if (!status) return "running";
        if (status === "success") return "complete";
        if (status === "manual_review_required") return "needs_review";
        if (status === "failed") return "unavailable";
        if (status === "needs_review" || status === "unavailable" || status === "complete" || status === "running") {
            return status;
        }
        return status;
    }

    function getStatusBadge(status) {
        var s = normalizeRunStatus(status);
        if (s === "complete") return { cls: "status-complete", text: "分析完成" };
        if (s === "needs_review") return { cls: "status-failed", text: "待人工复核" };
        if (s === "unavailable") return { cls: "status-failed", text: "结果暂不可用" };
        if (s === "running") return { cls: "status-running", text: "运行中" };
        if (s === "cancelled") return { cls: "status-cancelled", text: "已取消" };
        return { cls: "status-running", text: "处理中" };
    }

    /* ===== Router ===== */
    var AppRouter = {
        pages: ["new", "queue", "history", "stats", "report", "logs", "settings", "admin"],
        currentPage: "new",

        init: function () {
            var self = this;
            document.querySelectorAll(".nav-item").forEach(function (el) {
                el.addEventListener("click", function (e) {
                    e.preventDefault();
                    self.navigate(el.getAttribute("data-page"));
                    self._closeMobileSidebar();
                });
            });
            window.addEventListener("hashchange", function () {
                var hash = location.hash.replace("#", "");
                if (hash.startsWith("admin/user/")) {
                    var parts = hash.split("/");
                    var userId = parseInt(parts[2], 10);
                    if (!isNaN(userId) && typeof AdminPanel !== "undefined") {
                        AppRouter._showAdminPage();
                        AdminPanel.loadUserDetail(userId);
                    }
                    return;
                }
                var page = hash || "new";
                if (self.pages.indexOf(page) >= 0 && page !== self.currentPage) {
                    self.navigate(page, true);
                }
            });
            var hashPage = location.hash.replace("#", "");
            if (hashPage.startsWith("admin/user/")) {
                var parts = hashPage.split("/");
                var userId = parseInt(parts[2], 10);
                if (!isNaN(userId) && typeof AdminPanel !== "undefined") {
                    this._showAdminPage();
                    setTimeout(function () { AdminPanel.loadUserDetail(userId); }, 0);
                }
            } else if (hashPage && this.pages.indexOf(hashPage) >= 0) {
                this.navigate(hashPage, true);
            }
        },

        navigate: function (page, skipHash) {
            if (!page || this.pages.indexOf(page) < 0) page = "new";
            if (page === "admin") {
                try {
                    var currentUser = JSON.parse(localStorage.getItem("stem_tutor_user") || "null");
                    if (!currentUser || !currentUser.is_admin) page = "new";
                } catch (e) {
                    page = "new";
                }
            }

            document.querySelectorAll(".page").forEach(function (el) {
                el.classList.remove("active");
            });

            var target = $("page-" + page);
            if (target) target.classList.add("active");

            document.querySelectorAll(".nav-item").forEach(function (el) {
                el.classList.toggle("active", el.getAttribute("data-page") === page);
            });

            var titles = {
                new: "\u5206\u6790\u8bca\u65ad",
                queue: "\u6279\u91cf\u961f\u5217",
                history: "\u5386\u53f2\u8bb0\u5f55",
                stats: "\u7edf\u8ba1\u6982\u89c8",
                report: "\u5b66\u4e60\u62a5\u544a",
                logs: "\u8c03\u8bd5\u65e5\u5fd7",
                settings: "\u8bbe\u7f6e",
                admin: "\u7ba1\u7406\u5458\u9762\u677f"
            };
            var titleEl = $("page-title");
            if (titleEl) titleEl.textContent = titles[page] || page;
            document.title = (titles[page] || "") + " - " + SITE_TITLE;

            if (!skipHash) location.hash = page;
            this.currentPage = page;

            if (page === "history") HistoryModule.load();
            if (page === "queue") QueueModule.load();
            if (page === "stats") StatsModule.load();
            if (page === "report") ReportModule.init();
            if (page === "settings") SettingsModule.loadValues();
            if (page === "logs") LogsModule.init();
            if (page === "admin" && typeof AdminPanel !== "undefined") AdminPanel.load();
            var newBtn = $("btn-new-diagnosis");
            if (newBtn) newBtn.style.display = page === "new" ? "" : "none";
        },

        _closeMobileSidebar: function () {
            var sidebar = $("sidebar");
            var overlay = $("overlay");
            if (sidebar) sidebar.classList.remove("open");
            if (overlay) overlay.classList.remove("active");
        },

        _showAdminPage: function () {
            document.querySelectorAll(".page").forEach(function (el) {
                el.classList.remove("active");
            });
            var target = $("page-admin");
            if (target) target.classList.add("active");
            document.querySelectorAll(".nav-item").forEach(function (el) {
                el.classList.toggle("active", el.getAttribute("data-page") === "admin");
            });
            var titleEl = $("page-title");
            if (titleEl) titleEl.textContent = "管理员面板";
            document.title = "管理员面板 - " + SITE_TITLE;
            this.currentPage = "admin";
        }
    };

    /* ===== LogsModule ===== */
    var LogsModule = {
        _loaded: false,
        _currentData: null,
        _tabsBound: false,

        init: function () {
            if (!this._loaded) {
                this._loadRunList();
                this._loaded = true;
            }
            if (!this._tabsBound) {
                this._bindTabs();
                this._tabsBound = true;
            }
        },

        loadRunById: function (runId) {
            this.init();
            var sel = $("logs-run-select");
            if (sel) sel.value = runId;
            this._loadResult(runId);
        },

        _loadRunList: function () {
            var sel = $("logs-run-select");
            var currentVal = sel.value;
            sel.innerHTML = '<option value="">选择一次分析记录...</option>';
            fetch("/history?per_page=100", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var runs = data.runs || [];
                    runs.forEach(function (run) {
                        var opt = document.createElement("option");
                        opt.value = run.run_id;
                        var preview = (run.problem_preview || "无题目");
                        if (preview.length > 50) preview = preview.substring(0, 50) + "...";
                        opt.textContent = preview + "  [" + formatTimestamp(run.timestamp) + "]";
                        sel.appendChild(opt);
                    });
                    if (currentVal) sel.value = currentVal;
                });
            var self = this;
            sel.removeEventListener("change", sel._logsHandler);
            sel._logsHandler = function () {
                if (this.value) self._loadResult(this.value);
            };
            sel.addEventListener("change", sel._logsHandler);
            $("logs-refresh-btn").onclick = function () {
                self._loaded = false;
                self.init();
            };
        },

        _loadResult: function (runId) {
            var self = this;
            $("logs-content").style.display = "none";
            fetch("/analyze/result/" + runId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) {
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.json();
                })
                .then(function (data) {
                    self._currentData = data;
                    $("logs-content").style.display = "";
                    self._renderMeta(data);
                    self._renderState(data);
                    self._renderTools(data);
                    self._renderTrace(data);
                    self._renderRaw(data);
                })
                .catch(function (err) {
                    $("logs-content").style.display = "";
                    $("logs-meta").innerHTML = '<div class="alert alert-error">加载失败: ' + esc(err.message) + '</div>';
                });
        },

        _renderMeta: function (data) {
            var meta = data.run_meta || {};
            var html = '<div class="logs-meta-grid">';
            html += '<span><b>Run ID</b>: ' + esc(meta.run_id || "-") + '</span>';
            html += '<span><b>模式</b>: ' + esc(meta.mode || "-") + '</span>';
            html += '<span><b>模型</b>: ' + esc(meta.model || "-") + '</span>';
            html += '<span><b>状态</b>: ' + esc(data.user_status || data.status || "-") + '</span>';
            if (meta.started_at) html += '<span><b>开始</b>: ' + esc(formatTimestamp(meta.started_at)) + '</span>';
            if (meta.completed_at) html += '<span><b>完成</b>: ' + esc(formatTimestamp(meta.completed_at)) + '</span>';
            if (meta.ocr_model) html += '<span><b>OCR 模型</b>: ' + esc(meta.ocr_model) + '</span>';
            if (meta.provider) html += '<span><b>Provider</b>: ' + esc(meta.provider) + '</span>';
            if (data.fail_reason) html += '<span><b>失败原因</b>: ' + esc(data.fail_reason) + '</span>';
            html += '</div>';
            $("logs-meta").innerHTML = html;
        },

        _renderState: function (data) {
            var raw = data.raw_output || {};
            var groups = [
                { title: "输入", keys: ["problem_input", "raw_student_solution", "ocr_meta", "parse_warnings"] },
                { title: "解析结果", keys: ["normalized_steps"] },
                { title: "参考解答", keys: ["reference_solution", "reference_computation_hints"] },
                { title: "验证结果", keys: ["verification_results", "uncertainty_flags"] },
                { title: "诊断结果", keys: ["diagnosis_results"] },
                { title: "反馈与复习", keys: ["final_feedback", "review_problems"] },
                { title: "元数据", keys: ["run_meta", "budget_metadata", "quality_signals", "global_budget", "fail_reason"] }
            ];
            var html = "";
            for (var gi = 0; gi < groups.length; gi++) {
                var g = groups[gi];
                var hasContent = false;
                for (var ki = 0; ki < g.keys.length; ki++) {
                    var val = raw[g.keys[ki]];
                    if (val !== undefined && val !== null && (!(val instanceof Array) || val.length > 0) && (typeof val !== "object" || val instanceof Array || Object.keys(val).length > 0)) {
                        hasContent = true;
                        break;
                    }
                }
                html += '<details class="logs-group"' + (hasContent ? ' open' : '') + '>';
                html += '<summary class="logs-group-title">' + esc(g.title) + '</summary>';
                for (var kj = 0; kj < g.keys.length; kj++) {
                    var kval = raw[g.keys[kj]];
                    if (kval === undefined || kval === null) continue;
                    html += '<div class="logs-key-val">';
                    html += '<div class="logs-key">' + esc(g.keys[kj]) + '</div>';
                    html += '<div class="logs-val">' + LogsModule._renderValue(kval, 0) + '</div>';
                    html += '</div>';
                }
                html += '</details>';
            }
            $("logs-panel-state").innerHTML = html;
            if (window.renderMathInElement) {
                renderMathInElement($("logs-panel-state"), {
                    delimiters: [
                        { left: "$$", right: "$$", display: true },
                        { left: "$", right: "$", display: false },
                        { left: "\\(", right: "\\)", display: false },
                        { left: "\\[", right: "\\]", display: true }
                    ],
                    throwOnError: false, strict: false, trust: true
                });
            }
        },

        _renderValue: function (val, depth) {
            if (val === null || val === undefined) return '<span class="logs-null">null</span>';
            if (typeof val === "boolean") return '<span class="logs-bool">' + val + '</span>';
            if (typeof val === "number") return '<span class="logs-num">' + val + '</span>';
            if (typeof val === "string") {
                var escaped = esc(val);
                if (val.length > 300) {
                    return '<details><summary class="logs-string-summary">' + esc(val.substring(0, 100)) + '...</summary><pre class="logs-string-full">' + escaped + '</pre></details>';
                }
                return '<span class="logs-string">' + escaped + '</span>';
            }
            if (Array.isArray(val)) {
                if (val.length === 0) return '<span class="logs-empty">[] (empty)</span>';
                var arrHtml = '<span class="logs-array-badge">Array[' + val.length + ']</span>';
                arrHtml += '<ol class="logs-array">';
                for (var ai = 0; ai < val.length; ai++) {
                    arrHtml += '<li>' + LogsModule._renderValue(val[ai], depth + 1) + '</li>';
                }
                arrHtml += '</ol>';
                return arrHtml;
            }
            if (typeof val === "object") {
                var keys = Object.keys(val);
                if (keys.length === 0) return '<span class="logs-empty">{} (empty)</span>';
                var objHtml = '<details class="logs-obj"' + (depth < 2 ? ' open' : '') + '>';
                objHtml += '<summary>{' + keys.length + ' keys}</summary>';
                objHtml += '<table class="logs-obj-table">';
                for (var oi = 0; oi < keys.length; oi++) {
                    objHtml += '<tr><td class="logs-obj-key">' + esc(keys[oi]) + '</td><td>' + LogsModule._renderValue(val[keys[oi]], depth + 1) + '</td></tr>';
                }
                objHtml += '</table></details>';
                return objHtml;
            }
            return esc(String(val));
        },

        _renderTools: function (data) {
            var logs = data.tool_calls_log || [];
            var panel = $("logs-panel-tools");
            if (!logs.length) {
                panel.innerHTML = '<div class="logs-empty-msg">无工具调用记录</div>';
                return;
            }
            var html = '<div class="logs-timeline">';
            html += '<div class="logs-tool-summary">共 ' + logs.length + ' 次工具调用</div>';
            for (var i = 0; i < logs.length; i++) {
                var tc = logs[i];
                html += '<div class="logs-tool-card">';
                html += '<div class="logs-tool-header">';
                html += '<span class="logs-tool-index">#' + (i + 1) + '</span>';
                html += '<span class="logs-tool-name">' + esc(tc.tool_name || "?") + '</span>';
                html += '<span class="logs-tool-node">' + esc(tc.node || "?") + '</span>';
                if (tc.step_id) html += '<span class="logs-tool-step">Step: ' + esc(tc.step_id) + '</span>';
                if (tc.timestamp) html += '<span class="logs-tool-time">' + esc(tc.timestamp) + '</span>';
                html += '</div>';
                if (tc.code) html += '<pre class="logs-tool-code">' + esc(tc.code) + '</pre>';
                if (tc.result_preview) html += '<div class="logs-tool-result">' + esc(tc.result_preview) + '</div>';
                html += '</div>';
            }
            html += '</div>';
            panel.innerHTML = html;
        },

        _renderTrace: function (data) {
            var raw = data.raw_output || {};
            var traces = raw.trace || [];
            var panel = $("logs-panel-trace");
            if (!traces.length) {
                panel.innerHTML = '<div class="logs-empty-msg">无追踪记录</div>';
                return;
            }
            var html = '<div class="logs-trace-count">共 ' + traces.length + ' 条追踪</div>';
            html += '<ol class="logs-trace-list">';
            for (var ti = 0; ti < traces.length; ti++) {
                html += '<li class="logs-trace-item"><span class="logs-trace-idx">' + (ti + 1) + '</span> ' + esc(traces[ti]) + '</li>';
            }
            html += '</ol>';
            panel.innerHTML = html;
        },

        _renderRaw: function (data) {
            var raw = data.raw_output || {};
            var panel = $("logs-panel-raw");
            panel.innerHTML = "";
            var pre = document.createElement("pre");
            pre.className = "logs-raw-json";
            pre.textContent = JSON.stringify(raw, null, 2);
            panel.appendChild(pre);
        },

        _bindTabs: function () {
            var tabs = document.querySelectorAll(".logs-tab");
            var panels = ["logs-panel-state", "logs-panel-tools", "logs-panel-trace", "logs-panel-raw"];
            for (var i = 0; i < tabs.length; i++) {
                (function (tab) {
                    tab.addEventListener("click", function () {
                        for (var j = 0; j < tabs.length; j++) tabs[j].classList.remove("active");
                        for (var k = 0; k < panels.length; k++) $(panels[k]).classList.remove("active");
                        tab.classList.add("active");
                        $("logs-panel-" + tab.getAttribute("data-tab")).classList.add("active");
                    });
                })(tabs[i]);
            }
        }
    };

    /* ===== HistoryModule ===== */
    var _confirmCallback = null;

    var HistoryModule = {
        loaded: false,
        batchMode: false,
        selectedIds: {},
        _currentRuns: [],
        _page: 1,
        _perPage: 20,
        _total: 0,

        load: function () {
            var self = this;
            var params = [];
            var subject = $("history-filter-subject");
            var status = $("history-filter-status");
            var search = $("history-search");
            if (subject && subject.value) params.push("subject=" + encodeURIComponent(subject.value));
            if (status && status.value) params.push("status=" + encodeURIComponent(status.value));
            if (search && search.value.trim()) params.push("search=" + encodeURIComponent(search.value.trim()));
            params.push("page=" + this._page);
            params.push("per_page=" + this._perPage);

            fetch("/history?" + params.join("&"), { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self._currentRuns = data.runs || [];
                    self._total = data.total || 0;
                    self._page = data.page || 1;
                    self._perPage = data.per_page || 20;
                    self.render(self._currentRuns);
                    self.renderPagination();
                })
                .catch(function () {
                    $("history-list").innerHTML = '<div class="history-empty"><div class="history-empty-icon">\u26a0</div><p class="history-empty-title">\u52a0\u8f7d\u5931\u8d25</p><p class="history-empty-desc">\u8bf7\u68c0\u67e5\u7f51\u7edc\u8fde\u63a5</p></div>';
                    $("history-pagination").style.display = "none";
                });
        },

        render: function (runs) {
            var self = this;
            var container = $("history-list");
            if (!runs.length) {
                container.innerHTML = '<div class="history-empty"><div class="history-empty-icon">\ud83d\udcdc</div><p class="history-empty-title">\u6682\u65e0\u5386\u53f2\u8bb0\u5f55</p><p class="history-empty-desc">\u5f00\u59cb\u4f60\u7684\u7b2c\u4e00\u6b21\u8bca\u65ad\u5427\uff01</p><button type="button" class="btn-primary btn-sm" data-goto="new">\u524d\u5f80\u65b0\u5efa\u8bca\u65ad</button></div>';
                var gotoBtn = container.querySelector('[data-goto="new"]');
                if (gotoBtn) gotoBtn.addEventListener("click", function () { AppRouter.navigate("new"); });
                return;
            }

            var html = "";
            runs.forEach(function (run) {
                var badge = getStatusBadge(run.user_status || run.status);
                var statusCls = badge.cls;
                var statusText = badge.text;

                var cardCls = "history-card";
                if (self.batchMode) cardCls += " batch-mode";
                if (self.selectedIds[run.run_id]) cardCls += " selected";

                html += '<div class="' + cardCls + '" data-run-id="' + esc(run.run_id) + '">';
                if (self.batchMode) {
                    html += '<input type="checkbox" class="history-card-checkbox"'
                          + (self.selectedIds[run.run_id] ? ' checked' : '')
                          + ' data-run-id="' + esc(run.run_id) + '">';
                }
                html += '<div class="history-card-header">' + esc(run.problem_preview || "\u65e0\u9898\u76ee") + '</div>';
                html += '<div class="history-card-meta">';
                if (run.subject_display) html += '<span class="meta-item">\ud83d\udcda ' + esc(run.subject_display) + '</span>';
                if (run.timestamp) html += '<span class="meta-item">\ud83d\udd52 ' + formatTimestamp(run.timestamp) + '</span>';
                html += '<span class="meta-item"><span class="status-badge ' + statusCls + '">' + statusText + '</span></span>';
                if (run.duration_seconds) html += '<span class="meta-item">\u23f1 ' + formatDuration(run.duration_seconds) + '</span>';
                html += '</div>';
                html += '<div class="history-card-actions">';
                html += '<button type="button" class="btn-secondary" data-action="view" data-run-id="' + esc(run.run_id) + '">\u{1f441} \u67e5\u770b\u7ed3\u679c</button>';
                html += '<button type="button" class="btn-secondary" data-action="reanalyze" data-run-id="' + esc(run.run_id) + '">\ud83d\udd04 \u91cd\u65b0\u5206\u6790</button>';
                html += '<button type="button" class="btn-secondary" data-action="delete" data-run-id="' + esc(run.run_id) + '">\ud83d\udded \u5220\u9664</button>';
                html += '<button type="button" class="btn-secondary" data-action="debug" data-run-id="' + esc(run.run_id) + '">\ud83d\udd0d \u8c03\u8bd5</button>';
                html += '</div>';
                html += '</div>';
            });

            container.innerHTML = html;

            container.querySelectorAll('[data-action="view"]').forEach(function (btn) {
                btn.addEventListener("click", function (e) {
                    e.stopPropagation();
                    if (!self.batchMode) self.viewResult(btn.getAttribute("data-run-id"));
                });
            });
            container.querySelectorAll('[data-action="reanalyze"]').forEach(function (btn) {
                btn.addEventListener("click", function (e) {
                    e.stopPropagation();
                    if (!self.batchMode) self.reanalyze(btn.getAttribute("data-run-id"));
                });
            });
            container.querySelectorAll('[data-action="delete"]').forEach(function (btn) {
                btn.addEventListener("click", function (e) {
                    e.stopPropagation();
                    var runId = btn.getAttribute("data-run-id");
                    self.showConfirm(
                        "\u786e\u8ba4\u5220\u9664",
                        "\u5220\u9664\u540e\u65e0\u6cd5\u6062\u590d\uff0c\u786e\u5b9a\u5417\uff1f",
                        function () {
                            fetch("/api/runs", {
                                method: "DELETE",
                                headers: Object.assign({ "Content-Type": "application/json" }, AuthModule.getAuthHeader()),
                                body: JSON.stringify({"run_ids": [runId]})
                            }).then(function (r) { return r.json(); })
                            .then(function (data) { console.log("Delete response:", data); self.load(); })
                            .catch(function (e) { console.error("Delete failed:", e); self.load(); });
                        }
                    );
                });
            });
            container.querySelectorAll('[data-action="debug"]').forEach(function (btn) {
                btn.addEventListener("click", function (e) {
                    e.stopPropagation();
                    if (!self.batchMode) {
                        AppRouter.navigate("logs");
                        LogsModule.loadRunById(btn.getAttribute("data-run-id"));
                    }
                });
            });

            if (self.batchMode) {
                container.querySelectorAll(".history-card-checkbox").forEach(function (cb) {
                    cb.addEventListener("change", function (e) {
                        e.stopPropagation();
                        self.toggleSelect(cb.getAttribute("data-run-id"));
                    });
                });
                container.querySelectorAll(".history-card.batch-mode").forEach(function (card) {
                    card.addEventListener("click", function (e) {
                        if (e.target.tagName === "INPUT") return;
                        e.stopPropagation();
                        var rid = card.getAttribute("data-run-id");
                        self.toggleSelect(rid);
                        var cb = card.querySelector(".history-card-checkbox");
                        if (cb) cb.checked = !!self.selectedIds[rid];
                    });
                });
            }
        },

        enterBatchMode: function () {
            this.batchMode = true;
            this.selectedIds = {};
            $("history-toolbar").style.display = "";
            $("btn-enter-batch").style.display = "none";
            this.updateSelectionUI();
            this.render(this._currentRuns);
        },

        exitBatchMode: function () {
            this.batchMode = false;
            this.selectedIds = {};
            $("history-toolbar").style.display = "none";
            $("btn-enter-batch").style.display = "";
            this.render(this._currentRuns);
        },

        toggleSelect: function (runId) {
            if (this.selectedIds[runId]) {
                delete this.selectedIds[runId];
            } else {
                this.selectedIds[runId] = true;
            }
            this.updateSelectionUI();
            this._updateCardStyles();
        },

        toggleSelectAll: function () {
            var self = this;
            var allChecked = true;
            this._currentRuns.forEach(function (r) {
                if (!self.selectedIds[r.run_id]) allChecked = false;
            });
            if (allChecked) {
                this.selectedIds = {};
            } else {
                this._currentRuns.forEach(function (r) {
                    self.selectedIds[r.run_id] = true;
                });
            }
            this.updateSelectionUI();
            this._syncCheckboxes();
            this._updateCardStyles();
        },

        updateSelectionUI: function () {
            var count = Object.keys(this.selectedIds).length;
            $("history-selected-count").textContent = "\u5df2\u9009 " + count + " \u6761";
            $("batch-delete-count").textContent = count;
            $("btn-batch-delete").disabled = count === 0;
            var selectAll = $("history-select-all");
            if (selectAll) {
                var total = this._currentRuns.length;
                selectAll.checked = total > 0 && count === total;
            }
        },

        _syncCheckboxes: function () {
            var self = this;
            document.querySelectorAll(".history-card-checkbox").forEach(function (cb) {
                cb.checked = !!self.selectedIds[cb.getAttribute("data-run-id")];
            });
        },

        _updateCardStyles: function () {
            var self = this;
            document.querySelectorAll(".history-card.batch-mode").forEach(function (card) {
                var rid = card.getAttribute("data-run-id");
                if (self.selectedIds[rid]) {
                    card.classList.add("selected");
                } else {
                    card.classList.remove("selected");
                }
            });
        },

        batchDelete: function () {
            var ids = Object.keys(this.selectedIds);
            if (!ids.length) return;
            var self = this;
            this.showConfirm(
                "\u786e\u8ba4\u5220\u9664",
                "\u786e\u5b9a\u8981\u5220\u9664\u9009\u4e2d\u7684 " + ids.length + " \u6761\u8bb0\u5f55\u5417\uff1f\u6b64\u64cd\u4f5c\u4e0d\u53ef\u6062\u590d\u3002",
                function () {
                    fetch("/api/runs", {
                        method: "DELETE",
                        headers: Object.assign({ "Content-Type": "application/json" }, AuthModule.getAuthHeader()),
                        body: JSON.stringify({"run_ids": ids})
                    })
                    .then(function (r) { return r.json(); })
                    .then(function () {
                        self.selectedIds = {};
                        self._clampPage();
                        self.load();
                    });
                }
            );
        },

        cleanupByDate: function (days) {
            var label = days === 0 ? "\u5168\u90e8" : days + " \u5929\u524d";
            var self = this;
            this.showConfirm(
                "\u786e\u8ba4\u6e05\u7406",
                "\u786e\u5b9a\u8981\u6e05\u7406 " + label + " \u7684\u6240\u6709\u8bb0\u5f55\u5417\uff1f\u6b64\u64cd\u4f5c\u4e0d\u53ef\u6062\u590d\u3002",
                function () {
                    fetch("/api/runs/cleanup?before_days=" + days, { method: "POST", headers: AuthModule.getAuthHeader() })
                    .then(function (r) { return r.json(); })
                    .then(function () {
                        self._clampPage();
                        self.load();
                    });
                }
            );
        },

        showConfirm: function (title, message, onConfirm) {
            $("confirm-title").textContent = title;
            $("confirm-message").textContent = message;
            _confirmCallback = onConfirm;
            $("confirm-overlay").style.display = "";
            $("confirm-ok").onclick = function () {
                var cb = _confirmCallback;
                HistoryModule.hideConfirm();
                if (cb) cb();
            };
        },

        hideConfirm: function () {
            $("confirm-overlay").style.display = "none";
            _confirmCallback = null;
        },

        renderPagination: function () {
            var total = this._total;
            var page = this._page;
            var perPage = this._perPage;
            var totalPages = Math.ceil(total / perPage) || 1;

            if (total === 0) {
                $("history-pagination").style.display = "none";
                return;
            }
            $("history-pagination").style.display = "";

            var start = (page - 1) * perPage + 1;
            var end = Math.min(page * perPage, total);
            $("pagination-summary").textContent = "\u7b2c " + start + "-" + end + " \u6761\uff0c\u5171 " + total + " \u6761";

            $("btn-page-first").disabled = page <= 1;
            $("btn-page-prev").disabled = page <= 1;
            $("btn-page-next").disabled = page >= totalPages;
            $("btn-page-last").disabled = page >= totalPages;

            var pagesEl = $("pagination-pages");
            var pagesHtml = "";
            var maxVisible = 5;
            var startPage = Math.max(1, page - Math.floor(maxVisible / 2));
            var endPage = Math.min(totalPages, startPage + maxVisible - 1);
            if (endPage - startPage + 1 < maxVisible) {
                startPage = Math.max(1, endPage - maxVisible + 1);
            }

            if (startPage > 1) {
                pagesHtml += '<button type="button" class="page-btn" data-page="1">1</button>';
                if (startPage > 2) pagesHtml += '<span class="page-btn ellipsis">...</span>';
            }
            for (var i = startPage; i <= endPage; i++) {
                pagesHtml += '<button type="button" class="page-btn'
                    + (i === page ? " active" : "")
                    + '" data-page="' + i + '">' + i + '</button>';
            }
            if (endPage < totalPages) {
                if (endPage < totalPages - 1) pagesHtml += '<span class="page-btn ellipsis">...</span>';
                pagesHtml += '<button type="button" class="page-btn" data-page="' + totalPages + '">' + totalPages + '</button>';
            }
            pagesEl.innerHTML = pagesHtml;

            var self = this;
            pagesEl.querySelectorAll(".page-btn[data-page]").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var p = parseInt(btn.getAttribute("data-page"));
                    if (p !== self._page) {
                        self._page = p;
                        self.load();
                        $("page-history").scrollTop = 0;
                    }
                });
            });
        },

        _resetPage: function () {
            this._page = 1;
        },

        _clampPage: function () {
            var totalPages = Math.ceil(this._total / this._perPage) || 1;
            if (this._page > totalPages) this._page = totalPages;
        },

        viewResult: function (runId) {
            AppRouter.navigate("new");
            $("output-area").style.display = "";
            InputPanel.collapse();
            var loadingDiv = $("loading");
            if (loadingDiv) { loadingDiv.style.display = ""; loadingDiv.querySelector(".loading-title").textContent = "正在加载历史结果..."; }
            var resultsDiv = $("results");
            if (resultsDiv) resultsDiv.style.display = "none";

            fetch("/analyze/result/" + runId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || "HTTP " + r.status); });
                    return r.json();
                })
                .then(function (data) {
                    if (loadingDiv) loadingDiv.style.display = "none";
                    renderResults(data);
                    var outputArea = $("output-area");
                    if (outputArea) outputArea.scrollIntoView({ behavior: "smooth", block: "start" });
                })
                .catch(function (err) {
                    if (loadingDiv) loadingDiv.style.display = "none";
                    var banner = $("status-banner");
                    if (banner) {
                        banner.className = "alert alert-error";
                        banner.innerHTML = "<strong>\u52a0\u8f7d\u7ed3\u679c\u5931\u8d25\uff1a</strong>" + (err.message || "\u672a\u77e5\u9519\u8bef");
                        banner.style.display = "";
                    }
                    if (resultsDiv) resultsDiv.style.display = "none";
                });
        },

        reanalyze: function (runId) {
            fetch("/analyze/result/" + runId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var raw = data.raw_output || {};
                    var pi = raw.problem_input || {};
                    var pt = "";
                    if (typeof pi === "object" && pi.problem_text) pt = pi.problem_text;
                    var ss = raw.raw_student_solution || "";

                    $("problem-text").value = pt;
                    $("student-solution").value = ss;
                    AppRouter.navigate("new");
                    InputPanel.expand();
                    $("input-panel").scrollIntoView({ behavior: "smooth", block: "start" });
                })
                .catch(function () {
                    alert("\u52a0\u8f7d\u5931\u8d25");
                });
        }
    };

    /* ===== StatsModule ===== */
    var StatsModule = {
        charts: {},

        load: function () {
            var self = this;
            fetch("/stats", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self.renderSummary(data);
                    self.renderSubjectChart(data.subject_distribution || {});
                    self.renderTrendChart(data.daily_trend || []);
                })
                .catch(function () {});
        },

        renderSummary: function (data) {
            $("stat-total").textContent = data.total_runs || 0;
            $("stat-success-rate").textContent = ((data.success_rate || 0) * 100).toFixed(0) + "%";
            $("stat-avg-duration").textContent = formatDuration(data.avg_duration_seconds);
            $("stat-streak").textContent = data.streak_days || 0;
            $("stat-today").textContent = data.today_runs || 0;
            $("stat-week").textContent = data.week_runs || 0;
        },

        renderSubjectChart: function (distribution) {
            if (this.charts.subject) this.charts.subject.destroy();
            var canvas = $("subject-chart");
            if (!canvas || !window.Chart) return;

            var labels = Object.keys(distribution);
            var values = Object.values(distribution);
            var colors = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4", "#84cc16", "#f97316"];

            this.charts.subject = new Chart(canvas, {
                type: "doughnut",
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: colors.slice(0, labels.length)
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: "bottom", labels: { padding: 16, font: { size: 12 } } }
                    }
                }
            });
        },

        renderTrendChart: function (trend) {
            if (this.charts.trend) this.charts.trend.destroy();
            var canvas = $("trend-chart");
            if (!canvas || !window.Chart) return;

            var labels = trend.map(function (t) { return t.date.substring(5); });
            var values = trend.map(function (t) { return t.count; });

            this.charts.trend = new Chart(canvas, {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "\u8bca\u65ad\u6b21\u6570",
                        data: values,
                        borderColor: "#2563eb",
                        backgroundColor: "rgba(37,99,235,0.1)",
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointHoverRadius: 5
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, ticks: { stepSize: 1 } },
                        x: { grid: { display: false } }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }
    };

    /* ===== ReportModule ===== */
    var ReportModule = {
        chartRadar: null,
        selectedDays: 7,
        reportData: null,
        _initialized: false,
        selectionMode: "time",
        _allRuns: null,
        _filteredRuns: null,
        _selectedRunIds: {},
        _customDateActive: false,
        _generating: false,
        _pendingSections: [],

        init: function () {
            if (this._initialized) return;
            this._initialized = true;

            var self = this;

            document.querySelectorAll(".mode-tab").forEach(function (tab) {
                tab.addEventListener("click", function () {
                    document.querySelectorAll(".mode-tab").forEach(function (t) { t.classList.remove("active"); });
                    tab.classList.add("active");
                    self.selectionMode = tab.getAttribute("data-mode");
                    self._switchMode();
                });
            });

            document.querySelectorAll("#report-mode-time .time-btn").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    document.querySelectorAll("#report-mode-time .time-btn").forEach(function (b) { b.classList.remove("active"); });
                    btn.classList.add("active");
                    self.selectedDays = parseInt(btn.getAttribute("data-days")) || 0;
                    self._customDateActive = false;
                    self._updateDateHint();
                    if ($("report-start-date")) $("report-start-date").value = "";
                    if ($("report-end-date")) $("report-end-date").value = "";
                });
            });

            var startDateEl = $("report-start-date");
            var endDateEl = $("report-end-date");
            if (startDateEl) startDateEl.addEventListener("change", function () { self._onDateChange(); });
            if (endDateEl) endDateEl.addEventListener("change", function () { self._onDateChange(); });

            var genBtn = $("report-generate-btn");
            if (genBtn) genBtn.addEventListener("click", function () { self.generate(); });

            var retryBtn = $("report-retry-btn");
            if (retryBtn) retryBtn.addEventListener("click", function () { self.generate(); });

            var histBtn = $("report-history-btn");
            if (histBtn) histBtn.addEventListener("click", function () { self._showHistory(); });

            var histBack = $("report-history-back");
            if (histBack) histBack.addEventListener("click", function () { self._hideHistory(); });

            var runSearch = $("report-run-search");
            if (runSearch) runSearch.addEventListener("input", function () {
                clearTimeout(self._searchTimer);
                self._searchTimer = setTimeout(function () { self._filterRunList(); }, 300);
            });

            var runSubject = $("report-run-subject");
            if (runSubject) runSubject.addEventListener("change", function () { self._filterRunList(); });

            var selectAll = $("report-select-all");
            if (selectAll) selectAll.addEventListener("change", function () { self._toggleSelectAll(this.checked); });
        },

        _switchMode: function () {
            var timePanel = $("report-mode-time");
            var manualPanel = $("report-mode-manual");
            if (this.selectionMode === "time") {
                if (timePanel) timePanel.style.display = "";
                if (manualPanel) manualPanel.style.display = "none";
            } else {
                if (timePanel) timePanel.style.display = "none";
                if (manualPanel) manualPanel.style.display = "";
                if (!this._allRuns) this._loadRunList();
            }
        },

        _onDateChange: function () {
            var sd = $("report-start-date");
            var ed = $("report-end-date");
            if (sd && ed && sd.value && ed.value && sd.value > ed.value) {
                var tmp = sd.value;
                sd.value = ed.value;
                ed.value = tmp;
            }
            var hasCustom = (sd && sd.value) || (ed && ed.value);
            if (hasCustom) {
                document.querySelectorAll("#report-mode-time .time-btn").forEach(function (b) { b.classList.remove("active"); });
                this._customDateActive = true;
                this.selectedDays = 0;
            }
            this._updateDateHint();
        },

        _updateDateHint: function () {
            var hint = $("report-custom-hint");
            if (!hint) return;
            var sd = $("report-start-date");
            var ed = $("report-end-date");
            if (this._customDateActive && sd && ed && (sd.value || ed.value)) {
                var label = "自定义：" + (sd.value || "...") + " ~ " + (ed.value || "...");
                hint.textContent = label;
                hint.style.display = "";
            } else {
                hint.style.display = "none";
            }
        },

        _loadRunList: function () {
            var self = this;
            fetch("/report/runs", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (runs) {
                    self._allRuns = runs || [];
                    self._populateSubjectFilter();
                    self._filterRunList();
                })
                .catch(function () {
                    self._allRuns = [];
                    self._filterRunList();
                });
        },

        _populateSubjectFilter: function () {
            var sel = $("report-run-subject");
            if (!sel || !this._allRuns) return;
            var subjects = {};
            this._allRuns.forEach(function (r) {
                if (r.subject_id) subjects[r.subject_id] = r.subject_display || r.subject_id;
            });
            sel.innerHTML = '<option value="">全部科目</option>';
            Object.keys(subjects).sort().forEach(function (id) {
                var opt = document.createElement("option");
                opt.value = id;
                opt.textContent = subjects[id];
                sel.appendChild(opt);
            });
        },

        _filterRunList: function () {
            if (!this._allRuns) return;
            var search = $("report-run-search");
            var subject = $("report-run-subject");
            var q = search ? search.value.trim().toLowerCase() : "";
            var subj = subject ? subject.value : "";

            this._filteredRuns = this._allRuns.filter(function (r) {
                if (subj && r.subject_id !== subj) return false;
                if (q && !((r.problem_preview || "").toLowerCase().indexOf(q) >= 0)) return false;
                return true;
            });
            this._renderRunList();
        },

        _renderRunList: function () {
            var self = this;
            var container = $("report-run-list");
            if (!container) return;
            var runs = this._filteredRuns || [];

            if (runs.length === 0) {
                container.innerHTML = '<div class="report-run-list-empty">无匹配记录</div>';
                return;
            }

            var html = "";
            runs.forEach(function (r) {
                var sel = self._selectedRunIds[r.run_id] ? " selected" : "";
                var checked = self._selectedRunIds[r.run_id] ? " checked" : "";
                var ts = r.timestamp ? r.timestamp.substring(0, 16).replace("T", " ") : "";
                var statusLabels = { complete: "完成", needs_review: "待复核", unavailable: "不可用", success: "完成", failed: "失败" };
                var stLabel = statusLabels[r.status] || r.status || "";
                html += '<div class="report-run-item' + sel + '" data-run-id="' + esc(r.run_id) + '">';
                html += '<input type="checkbox"' + checked + ' data-run-id="' + esc(r.run_id) + '">';
                html += '<div class="report-run-item-info">';
                html += '<div class="report-run-item-preview">' + esc(r.problem_preview || "无题目") + '</div>';
                html += '<div class="report-run-item-meta">';
                html += '<span class="report-run-item-subject">' + esc(r.subject_display || r.subject_id || "") + '</span>';
                html += '<span class="report-run-item-status">' + esc(ts) + '</span>';
                html += '<span class="report-run-item-status">' + esc(stLabel) + '</span>';
                html += '</div></div></div>';
            });
            container.innerHTML = html;

            container.querySelectorAll(".report-run-item").forEach(function (item) {
                item.addEventListener("click", function (e) {
                    if (e.target.tagName === "INPUT") return;
                    var cb = item.querySelector('input[type="checkbox"]');
                    if (cb) cb.checked = !cb.checked;
                    self._toggleRunSelection(item.getAttribute("data-run-id"), cb ? cb.checked : undefined);
                });
            });
            container.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
                cb.addEventListener("change", function () {
                    self._toggleRunSelection(cb.getAttribute("data-run-id"), cb.checked);
                });
            });

            this._updateSelectedCount();
        },

        _toggleRunSelection: function (runId, isChecked) {
            if (isChecked === undefined) {
                isChecked = !this._selectedRunIds[runId];
            }
            if (isChecked) {
                this._selectedRunIds[runId] = true;
            } else {
                delete this._selectedRunIds[runId];
            }
            var item = $('report-run-list').querySelector('.report-run-item[data-run-id="' + runId + '"]');
            if (item) {
                if (isChecked) item.classList.add("selected");
                else item.classList.remove("selected");
            }
            this._updateSelectedCount();
        },

        _toggleSelectAll: function (checked) {
            var self = this;
            var runs = this._filteredRuns || [];
            runs.forEach(function (r) {
                if (checked) {
                    self._selectedRunIds[r.run_id] = true;
                } else {
                    delete self._selectedRunIds[r.run_id];
                }
            });
            this._renderRunList();
        },

        _updateSelectedCount: function () {
            var el = $("report-selected-count");
            if (el) el.textContent = "已选 " + Object.keys(this._selectedRunIds).length + " 条";
        },

        generate: function () {
            var self = this;
            var model = $("report-model");
            var modelName = model ? model.value : "qwen/qwen3.6-plus";

            var params;
            if (this.selectionMode === "manual") {
                var ids = Object.keys(this._selectedRunIds);
                if (ids.length === 0) {
                    self._showError("请至少选择一条诊断记录");
                    return;
                }
                params = { run_ids: ids.join(",") };
            } else {
                var sd = $("report-start-date");
                var ed = $("report-end-date");
                if (this._customDateActive && ((sd && sd.value) || (ed && ed.value))) {
                    params = { start_date: sd ? sd.value : "", end_date: ed ? ed.value : "" };
                } else {
                    params = { days: this.selectedDays };
                }
            }

            this._showLoading(true);
            this._hideAll();
            this._generating = true;
            this._pendingSections = [];

            var qs = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
            fetch("/report/data?" + qs, { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self.reportData = data;
                    if (data.total_runs < 1) {
                        self._generating = false;
                        self._showLoading(false);
                        $("report-empty").style.display = "";
                        return;
                    }
                    self._renderCharts(data);
                    self._fetchReport(modelName, params);
                })
                .catch(function (err) {
                    self._generating = false;
                    self._showLoading(false);
                    self._showError("获取数据失败：" + err.message);
                });
        },

        _fetchReport: function (modelName, params) {
            var self = this;
            var formData = new FormData();
            formData.append("model", modelName);
            if (params.days !== undefined) formData.append("days", params.days);
            if (params.start_date) formData.append("start_date", params.start_date);
            if (params.end_date) formData.append("end_date", params.end_date);
            if (params.run_ids) formData.append("run_ids", params.run_ids);

            fetch("/report/generate", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData, signal: AbortSignal.timeout(600000) })
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (d) { throw new Error(d.error || "生成失败"); });
                    return r.body;
                })
                .then(function (body) {
                    $("report-content").style.display = "";
                    $("report-content").innerHTML = "";
                    self._readReportSSE(body);
                })
                .catch(function (err) {
                    self._showLoading(false);
                    self._showError(err.message);
                });
        },

        _readReportSSE: function (body) {
            var self = this;
            var reader = body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buffer = "";
            var doneReceived = false;

            function readNext() {
                return reader.read().then(function (chunk) {
                    if (chunk.done) {
                        if (!doneReceived) {
                            console.error("[ReportSSE] Stream ended without report_done");
                            self._generating = false;
                            self._showLoading(false);
                            self._showError("响应意外结束，请重试。若问题持续，请检查网络或联系管理员。");
                        }
                        return;
                    }
                    buffer += decoder.decode(chunk.value, { stream: true });
                    var lines = buffer.split("\n");
                    buffer = lines.pop() || "";
                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i].trim();
                        if (!line || !line.startsWith("data:")) continue;
                        var jsonStr = line.substring(5).trim();
                        try {
                            var event = JSON.parse(jsonStr);
                            if (event.type === "report_progress") {
                                console.log("[ReportSSE] progress:", event.message);
                            } else if (event.type === "report_section") {
                                self._pendingSections.push(event.section);
                                self._showLoading(false);
                                self._renderSection(event.section);
                            } else if (event.type === "report_done") {
                                doneReceived = true;
                                self._generating = false;
                                self._showLoading(false);
                                return;
                            } else if (event.type === "report_error") {
                                doneReceived = true;
                                self._generating = false;
                                self._showLoading(false);
                                self._showError(event.message);
                            }
                        } catch (e) {
                            console.warn("[ReportSSE] Parse error:", e, jsonStr);
                        }
                    }
                    return readNext();
                });
            }
            return readNext();
        },

        _renderCharts: function (data) {
            if (this.chartRadar) { this.chartRadar.destroy(); this.chartRadar = null; }

            var radarData = data.radar_data || {};
            var subjects = Object.keys(radarData);
            if (subjects.length === 0) return;

            var allCategories = {};
            subjects.forEach(function (s) {
                Object.keys(radarData[s]).forEach(function (c) { allCategories[c] = true; });
            });
            var categories = Object.keys(allCategories);

            var colors = [
                "rgba(231, 76, 60, 0.7)", "rgba(52, 152, 219, 0.7)",
                "rgba(46, 204, 113, 0.7)", "rgba(243, 156, 18, 0.7)",
                "rgba(155, 89, 182, 0.7)", "rgba(26, 188, 156, 0.7)",
                "rgba(230, 126, 34, 0.7)", "rgba(149, 165, 166, 0.7)"
            ];
            var borderColors = colors.map(function (c) { return c.replace("0.7", "1"); });

            var datasets = subjects.map(function (subj, idx) {
                return {
                    label: subj,
                    data: categories.map(function (cat) { return radarData[subj][cat] || 0; }),
                    backgroundColor: colors[idx % colors.length],
                    borderColor: borderColors[idx % borderColors.length],
                    borderWidth: 2,
                    pointRadius: 3,
                };
            });

            var radarCanvas = document.createElement("canvas");
            radarCanvas.id = "report-radar-canvas";
            radarCanvas.style.maxHeight = "360px";

            this._radarCanvas = radarCanvas;
            this._radarConfig = {
                type: "radar",
                data: { labels: categories, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    scales: {
                        r: { beginAtZero: true, ticks: { stepSize: 1 } }
                    },
                    plugins: {
                        legend: { position: "bottom" }
                    }
                }
            };
        },

        _renderSection: function (section) {
            var container = $("report-content");
            if (!container || !section) return;

            var div = document.createElement("div");
            div.className = "report-section section-" + (section.type || "").replace(/_/g, "-");

            var titleDiv = document.createElement("div");
            titleDiv.className = "report-section-title";
            titleDiv.innerHTML = '<span class="section-icon">' + (section.icon || "") + "</span>" + (section.title || "");
            div.appendChild(titleDiv);

            if (section.summary) {
                var summaryP = document.createElement("div");
                summaryP.className = "report-summary";
                summaryP.textContent = section.summary;
                div.appendChild(summaryP);
            }

            if (section.type === "error_patterns") {
                this._renderRadarChart(div);
                if (section.items) this._renderErrorItems(div, section.items);
            } else if (section.type === "blind_spots") {
                this._renderHeatmap(div);
                if (section.items) this._renderBlindSpotItems(div, section.items);
            } else if (section.type === "error_evolution") {
                if (section.timeline) this._renderTimeline(div, section.timeline);
            } else if (section.type === "improvements") {
                if (section.items) this._renderImprovements(div, section.items);
            } else if (section.type === "action_plan") {
                if (section.priorities) this._renderPriorities(div, section.priorities);
            }

            container.appendChild(div);
        },

        _renderRadarChart: function (container) {
            if (!this._radarCanvas) return;
            var chartDiv = document.createElement("div");
            chartDiv.className = "report-chart-container";
            var canvas = document.createElement("canvas");
            canvas.id = "report-radar-" + Date.now();
            chartDiv.appendChild(canvas);
            container.appendChild(chartDiv);

            var ctx = canvas.getContext("2d");
            new Chart(ctx, this._radarConfig);
        },

        _renderHeatmap: function (container) {
            var data = this.reportData;
            if (!data || !data.heatmap_data) return;
            var hm = data.heatmap_data;
            var skills = hm.skills || [];
            var subjects = hm.subjects || [];
            var matrix = hm.matrix || [];
            if (skills.length === 0 || subjects.length === 0) return;

            var wrapper = document.createElement("div");
            wrapper.className = "report-chart-container";

            var table = document.createElement("table");
            table.className = "report-heatmap-table";

            var thead = document.createElement("thead");
            var headerRow = document.createElement("tr");
            var thEmpty = document.createElement("th");
            headerRow.appendChild(thEmpty);
            subjects.forEach(function (s) {
                var th = document.createElement("th");
                th.textContent = s;
                headerRow.appendChild(th);
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            var tbody = document.createElement("tbody");
            for (var i = 0; i < skills.length; i++) {
                var row = document.createElement("tr");
                var tdLabel = document.createElement("td");
                tdLabel.className = "skill-label";
                tdLabel.textContent = skills[i];
                row.appendChild(tdLabel);

                for (var j = 0; j < subjects.length; j++) {
                    var td = document.createElement("td");
                    var val = (matrix[i] && matrix[i][j] !== undefined) ? matrix[i][j] : 1.0;
                    var pct = Math.round(val * 100);
                    td.textContent = pct + "%";
                    td.style.color = "#fff";
                    var hue = Math.round(val * 120);
                    td.style.backgroundColor = "hsl(" + hue + ", 60%, 45%)";
                    row.appendChild(td);
                }
                tbody.appendChild(row);
            }
            table.appendChild(tbody);
            wrapper.appendChild(table);
            container.appendChild(wrapper);
        },

        _renderErrorItems: function (container, items) {
            var itemsDiv = document.createElement("div");
            itemsDiv.className = "report-items";
            items.forEach(function (item) {
                var itemDiv = document.createElement("div");
                itemDiv.className = "report-item";

                var header = document.createElement("div");
                header.className = "report-item-header";

                var nameSpan = document.createElement("span");
                nameSpan.className = "report-item-name";
                nameSpan.textContent = item.name || "";
                header.appendChild(nameSpan);

                if (item.count) {
                    var countBadge = document.createElement("span");
                    countBadge.className = "report-item-badge count";
                    countBadge.textContent = item.count + " 次";
                    header.appendChild(countBadge);
                }
                if (item.recurring) {
                    var recBadge = document.createElement("span");
                    recBadge.className = "report-item-badge recurring";
                    recBadge.textContent = "反复出现";
                    header.appendChild(recBadge);
                }
                if (item.related_subjects) {
                    item.related_subjects.forEach(function (s) {
                        var subBadge = document.createElement("span");
                        subBadge.className = "report-item-badge subject-tag";
                        subBadge.textContent = s;
                        header.appendChild(subBadge);
                    });
                }
                itemDiv.appendChild(header);

                if (item.analysis) {
                    var analysisP = document.createElement("div");
                    analysisP.className = "report-item-analysis";
                    analysisP.textContent = item.analysis;
                    itemDiv.appendChild(analysisP);
                }
                itemsDiv.appendChild(itemDiv);
            });
            container.appendChild(itemsDiv);
        },

        _renderBlindSpotItems: function (container, items) {
            var itemsDiv = document.createElement("div");
            itemsDiv.className = "report-items";
            items.forEach(function (item) {
                var itemDiv = document.createElement("div");
                itemDiv.className = "report-item";

                var header = document.createElement("div");
                header.className = "report-item-header";

                var nameSpan = document.createElement("span");
                nameSpan.className = "report-item-name";
                nameSpan.textContent = item.name || "";
                header.appendChild(nameSpan);

                if (item.severity) {
                    var sevBadge = document.createElement("span");
                    sevBadge.className = "report-item-badge severity-" + item.severity;
                    var sevLabels = { high: "严重", medium: "中等", low: "轻微" };
                    sevBadge.textContent = sevLabels[item.severity] || item.severity;
                    header.appendChild(sevBadge);
                }
                if (item.subject) {
                    var subBadge = document.createElement("span");
                    subBadge.className = "report-item-badge subject-tag";
                    subBadge.textContent = item.subject;
                    header.appendChild(subBadge);
                }
                itemDiv.appendChild(header);

                if (item.analysis) {
                    var analysisP = document.createElement("div");
                    analysisP.className = "report-item-analysis";
                    analysisP.textContent = item.analysis;
                    itemDiv.appendChild(analysisP);
                }
                if (item.mastery !== undefined && item.mastery !== null) {
                    var barDiv = document.createElement("div");
                    barDiv.className = "report-mastery-bar";
                    barDiv.innerHTML = '<span>掌握度</span><div class="report-mastery-track"><div class="report-mastery-fill" style="width:' + Math.round(item.mastery * 100) + "%;background:hsl(" + Math.round(item.mastery * 120) + ',60%,45%)"></div></div><span>' + Math.round(item.mastery * 100) + "%</span>";
                    itemDiv.appendChild(barDiv);
                }
                itemsDiv.appendChild(itemDiv);
            });
            container.appendChild(itemsDiv);
        },

        _renderTimeline: function (container, timeline) {
            var timelineDiv = document.createElement("div");
            timelineDiv.className = "report-timeline";
            timeline.forEach(function (item) {
                var itemDiv = document.createElement("div");
                itemDiv.className = "report-timeline-item";

                var periodDiv = document.createElement("div");
                periodDiv.className = "report-timeline-period";
                var trendLabels = { improving: "改善 ↑", worsening: "恶化 ↓", stable: "稳定 →", shifting: "转变 ↗" };
                periodDiv.innerHTML = item.period + ' <span class="report-timeline-trend ' + (item.trend || "stable") + '">' + (trendLabels[item.trend] || item.trend) + "</span>";
                itemDiv.appendChild(periodDiv);

                if (item.description) {
                    var descDiv = document.createElement("div");
                    descDiv.className = "report-timeline-desc";
                    descDiv.textContent = item.description;
                    itemDiv.appendChild(descDiv);
                }
                timelineDiv.appendChild(itemDiv);
            });
            container.appendChild(timelineDiv);
        },

        _renderImprovements: function (container, items) {
            var itemsDiv = document.createElement("div");
            itemsDiv.className = "report-items";
            items.forEach(function (item) {
                var itemDiv = document.createElement("div");
                itemDiv.className = "report-improvement-item";

                var check = document.createElement("span");
                check.className = "report-improvement-check";
                check.textContent = "✓";
                itemDiv.appendChild(check);

                var body = document.createElement("div");
                body.className = "report-improvement-body";

                var descDiv = document.createElement("div");
                descDiv.className = "report-improvement-desc";
                descDiv.textContent = item.description || "";
                body.appendChild(descDiv);

                if (item.evidence) {
                    var evDiv = document.createElement("div");
                    evDiv.className = "report-improvement-evidence";
                    evDiv.textContent = "依据：" + item.evidence;
                    body.appendChild(evDiv);
                }
                itemDiv.appendChild(body);
                itemsDiv.appendChild(itemDiv);
            });
            container.appendChild(itemsDiv);
        },

        _renderPriorities: function (container, priorities) {
            var priDiv = document.createElement("div");
            priDiv.className = "report-priorities";
            priorities.forEach(function (item) {
                var itemDiv = document.createElement("div");
                itemDiv.className = "report-priority";

                var header = document.createElement("div");
                header.className = "report-priority-header";

                var levelClass = { "优先": "level-urgent", "建议": "level-suggest", "保持": "level-maintain" };
                var levelSpan = document.createElement("span");
                levelSpan.className = "report-priority-level " + (levelClass[item.level] || "");
                levelSpan.textContent = item.level || "";
                header.appendChild(levelSpan);

                var focusSpan = document.createElement("span");
                focusSpan.className = "report-priority-focus";
                focusSpan.textContent = item.focus || "";
                header.appendChild(focusSpan);
                itemDiv.appendChild(header);

                if (item.reason) {
                    var reasonP = document.createElement("div");
                    reasonP.className = "report-priority-reason";
                    reasonP.textContent = item.reason;
                    itemDiv.appendChild(reasonP);
                }

                if (item.actions && item.actions.length > 0) {
                    var ul = document.createElement("ul");
                    ul.className = "report-priority-actions";
                    item.actions.forEach(function (a) {
                        var li = document.createElement("li");
                        li.textContent = a;
                        ul.appendChild(li);
                    });
                    itemDiv.appendChild(ul);
                }
                priDiv.appendChild(itemDiv);
            });
            container.appendChild(priDiv);
        },

        _showLoading: function (show) {
            var el = $("report-loading");
            if (el) el.style.display = show ? "" : "none";
        },

        _hideAll: function () {
            ["report-empty", "report-error", "report-content", "report-history"].forEach(function (id) {
                var el = $(id);
                if (el) el.style.display = "none";
            });
        },

        _showError: function (msg) {
            this._showLoading(false);
            var errDiv = $("report-error");
            var msgEl = $("report-error-msg");
            if (errDiv) errDiv.style.display = "";
            if (msgEl) msgEl.textContent = msg || "生成失败";
        },

        _formatBeijingTime: function (ts) {
            if (!ts) return "";
            var d = new Date(ts);
            if (isNaN(d.getTime())) return ts;
            return new Intl.DateTimeFormat("zh-CN", {
                timeZone: "Asia/Shanghai",
                year: "numeric",
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            }).format(d).replace(/\//g, "-");
        },

        _showHistory: function (page = 1) {
            var self = this;
            self._hideAll();
            if (!self._generating) {
                self._showLoading(false);
            }

            var histEl = $("report-history");
            if (histEl) histEl.style.display = "";
            var histList = $("report-history-list");
            if (histList) histList.innerHTML = '<div class="spinner"></div>';

            fetch("/report/list?page=" + page + "&per_page=10", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self._renderHistoryList(data);
                })
                .catch(function (err) {
                    if (histList) histList.innerHTML = '<p class="report-error">加载失败：' + err.message + '</p>';
                });
        },

        _hideHistory: function () {
            var histEl = $("report-history");
            if (histEl) histEl.style.display = "none";
            if (this._generating) {
                this._showLoading(true);
            } else if (this._pendingSections.length > 0) {
                var content = $("report-content");
                if (content && content.innerHTML) {
                    $("report-content").style.display = "";
                } else {
                    this._showLoading(false);
                }
            } else {
                this._showLoading(false);
            }
        },

        _renderHistoryList: function (data) {
            var self = this;
            var histList = $("report-history-list");
            var histEmpty = $("report-history-empty");
            var histPagination = $("report-history-pagination");
            if (!histList) return;

            histList.innerHTML = "";

            if (!data.reports || data.reports.length === 0) {
                if (histEmpty) histEmpty.style.display = "";
                if (histPagination) histPagination.style.display = "none";
                return;
            }
            if (histEmpty) histEmpty.style.display = "none";

            data.reports.forEach(function (r) {
                var card = document.createElement("div");
                card.className = "report-history-card";

                var info = document.createElement("div");
                info.className = "report-history-card-info";

                var title = document.createElement("div");
                title.className = "report-history-card-title";
                title.textContent = r.title || "未命名报告";

                var meta = document.createElement("div");
                meta.className = "report-history-card-meta";
                var dateStr = self._formatBeijingTime(r.created_at);
                meta.textContent = dateStr + "  ·  " + (r.total_runs || 0) + " 条记录  ·  " + (r.section_count || 0) + " 个章节  ·  " + (r.model || "");

                info.appendChild(title);
                info.appendChild(meta);

                var actions = document.createElement("div");
                actions.className = "report-history-card-actions";

                var viewBtn = document.createElement("button");
                viewBtn.className = "btn-secondary";
                viewBtn.textContent = "查看";
                viewBtn.addEventListener("click", function () { self._viewReport(r.report_id); });

                var delBtn = document.createElement("button");
                delBtn.className = "btn-secondary btn-danger";
                delBtn.textContent = "删除";
                delBtn.addEventListener("click", function () {
                    if (confirm("确定删除这份报告？")) { self._deleteReport(r.report_id); }
                });

                actions.appendChild(viewBtn);
                actions.appendChild(delBtn);

                card.appendChild(info);
                card.appendChild(actions);
                histList.appendChild(card);
            });

            if (histPagination && data.total > data.per_page) {
                histPagination.style.display = "";
                histPagination.innerHTML = "";
                var totalPages = Math.ceil(data.total / data.per_page);
                for (var p = 1; p <= totalPages; p++) {
                    var btn = document.createElement("button");
                    btn.textContent = p;
                    btn.className = "pagination-btn" + (p === data.page ? " active" : "");
                    (function (pg) {
                        btn.addEventListener("click", function () { self._showHistory(pg); });
                    })(p);
                    histPagination.appendChild(btn);
                }
            } else if (histPagination) {
                histPagination.style.display = "none";
            }
        },

        _viewReport: function (reportId) {
            var self = this;
            self._hideHistory();
            self._hideAll();
            self._showLoading(true);

            fetch("/report/" + reportId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) {
                    if (!r.ok) throw new Error("报告不存在");
                    return r.json();
                })
                .then(function (report) {
                    self._showLoading(false);
                    self.reportData = report.report_data;
                    $("report-content").style.display = "";
                    $("report-content").innerHTML = "";
                    if (report.report_data) self._renderCharts(report.report_data);
                    var sections = report.sections || [];
                    sections.forEach(function (section) {
                        self._renderSection(section);
                    });
                })
                .catch(function (err) {
                    self._showLoading(false);
                    self._showError("加载报告失败：" + err.message);
                });
        },

        _deleteReport: function (reportId) {
            var self = this;
            fetch("/report/" + reportId, { method: "DELETE", headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function () {
                    self._showHistory(1);
                })
                .catch(function (err) {
                    alert("删除失败：" + err.message);
                });
        }
    };

    /* ===== SettingsModule ===== */
    var SettingsModule = {
        defaults: {
            theme: "light",
            defaultSubject: "auto_detect",
            defaultDepth: "with_ref",
            defaultMode: "workflow_r1",
            defaultModel: "qwen/qwen3.6-plus",
            autoSaveHistory: true
        },

        init: function () {
            var self = this;
            var saved = localStorage.getItem("stem_tutor_settings");
            this.settings = saved ? Object.assign({}, this.defaults, JSON.parse(saved)) : Object.assign({}, this.defaults);
            this.applyTheme(this.settings.theme);
            this.applyDefaults();
            this.bindEvents();
            fetch("/api/user/settings", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
                .then(function (data) {
                    if (data && typeof data === "object" && Object.keys(data).length > 0) {
                        self.settings = Object.assign({}, self.defaults, data);
                        localStorage.setItem("stem_tutor_settings", JSON.stringify(self.settings));
                        self.applyTheme(self.settings.theme);
                        self.applyDefaults();
                    }
                }).catch(function () {});
        },

        get: function (key) {
            return this.settings[key] !== undefined ? this.settings[key] : this.defaults[key];
        },

        set: function (key, value) {
            this.settings[key] = value;
            localStorage.setItem("stem_tutor_settings", JSON.stringify(this.settings));
            fetch("/api/user/settings", {
                method: "POST",
                headers: Object.assign({ "Content-Type": "application/json" }, AuthModule.getAuthHeader()),
                body: JSON.stringify(this.settings)
            }).catch(function () {});
        },

        applyTheme: function (theme) {
            if (theme === "dark") {
                document.body.setAttribute("data-theme", "dark");
            } else if (theme === "auto") {
                var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                document.body.setAttribute("data-theme", prefersDark ? "dark" : "light");
            } else {
                document.body.removeAttribute("data-theme");
            }
        },

        applyDefaults: function () {
            var subjectSelect = $("subject-select");
            if (subjectSelect && this.settings.defaultSubject && this.settings.defaultSubject !== "auto_detect") {
                subjectSelect.value = this.settings.defaultSubject;
            }
            var depthRadios = document.querySelectorAll('input[name="depth"]');
            depthRadios.forEach(function (r) {
                if (r.value === this.settings.defaultDepth) { r.checked = true; r.dispatchEvent(new Event("change")); }
            }.bind(this));
            var modeSelect = $("mode-select");
            if (modeSelect) modeSelect.value = this.settings.defaultMode;
            var modelSelect = $("model-select");
            if (modelSelect) modelSelect.value = this.settings.defaultModel;
        },

        loadValues: function () {
            $("setting-theme").value = this.settings.theme;
            $("setting-default-subject").value = this.settings.defaultSubject;
            $("setting-default-depth").value = this.settings.defaultDepth;
            $("setting-default-mode").value = this.settings.defaultMode;
            $("setting-default-model").value = this.settings.defaultModel;
            $("setting-auto-save").checked = this.settings.autoSaveHistory;
        },

        bindEvents: function () {
            var self = this;
            var bind = function (id, key, isCheckbox) {
                var el = $(id);
                if (!el) return;
                el.addEventListener("change", function () {
                    var val = isCheckbox ? el.checked : el.value;
                    self.set(key, val);
                    if (key === "theme") self.applyTheme(val);
                    if (key === "defaultSubject" || key === "defaultDepth" || key === "defaultMode" || key === "defaultModel") {
                        self.applyDefaults();
                    }
                });
            };
            bind("setting-theme", "theme");
            bind("setting-default-subject", "defaultSubject");
            bind("setting-default-depth", "defaultDepth");
            bind("setting-default-mode", "defaultMode");
            bind("setting-default-model", "defaultModel");
            bind("setting-auto-save", "autoSaveHistory", true);

            var changePwdBtn = $("btn-change-password");
            if (changePwdBtn) {
                changePwdBtn.addEventListener("click", function () {
                    var modal = $("password-modal");
                    if (modal) modal.style.display = "flex";
                    var pwdOld = $("pwd-old"); if (pwdOld) pwdOld.value = "";
                    var pwdNew = $("pwd-new"); if (pwdNew) pwdNew.value = "";
                    var pwdConfirm = $("pwd-confirm"); if (pwdConfirm) pwdConfirm.value = "";
                    var pwdErr = $("pwd-error"); if (pwdErr) pwdErr.style.display = "none";
                });
            }
            var pwdModalClose = $("password-modal-close");
            if (pwdModalClose) pwdModalClose.addEventListener("click", function () { var m = $("password-modal"); if (m) m.style.display = "none"; });
            var pwdCancelBtn = $("pwd-cancel-btn");
            if (pwdCancelBtn) pwdCancelBtn.addEventListener("click", function () { var m = $("password-modal"); if (m) m.style.display = "none"; });
            var pwdConfirmBtn = $("pwd-confirm-btn");
            if (pwdConfirmBtn) {
                pwdConfirmBtn.addEventListener("click", function () {
                    var errorEl = $("pwd-error");
                    var oldPwd = ($("pwd-old").value || "");
                    var newPwd = ($("pwd-new").value || "");
                    var confirmPwd = ($("pwd-confirm").value || "");
                    if (!oldPwd || !newPwd || !confirmPwd) {
                        if (errorEl) { errorEl.textContent = "请填写所有字段"; errorEl.style.display = ""; }
                        return;
                    }
                    if (newPwd !== confirmPwd) {
                        if (errorEl) { errorEl.textContent = "两次输入的新密码不一致"; errorEl.style.display = ""; }
                        return;
                    }
                    if (newPwd.length < 4) {
                        if (errorEl) { errorEl.textContent = "新密码至少4位"; errorEl.style.display = ""; }
                        return;
                    }
                    pwdConfirmBtn.textContent = "修改中...";
                    pwdConfirmBtn.disabled = true;
                    fetch("/api/user/change-password", {
                        method: "POST",
                        headers: Object.assign({ "Content-Type": "application/json" }, AuthModule.getAuthHeader()),
                        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd })
                    }).then(function (r) {
                        return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || "修改失败"); return d; });
                    }).then(function () {
                        var modal = $("password-modal");
                        if (modal) modal.style.display = "none";
                        alert("密码修改成功");
                    }).catch(function (err) {
                        if (errorEl) { errorEl.textContent = err.message || "修改失败"; errorEl.style.display = ""; }
                    }).finally(function () {
                        pwdConfirmBtn.textContent = "确认修改";
                        pwdConfirmBtn.disabled = false;
                    });
                });
            }

            var exportBtn = $("btn-export-data");
            if (exportBtn) {
                exportBtn.addEventListener("click", function () {
                    fetch("/history?per_page=9999", { headers: AuthModule.getAuthHeader() })
                        .then(function (r) { return r.json(); })
                        .then(function (data) {
                            var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
                            var url = URL.createObjectURL(blob);
                            var a = document.createElement("a");
                            a.href = url;
                            a.download = "stem_tutor_history_" + new Date().toISOString().slice(0, 10) + ".json";
                            a.click();
                            URL.revokeObjectURL(url);
                        });
                });
            }
        }
    };

    /* ===== InputPanel ===== */
    var InputPanel = {
        init: function () {
            var header = $("input-panel-header");
            if (header) {
                header.addEventListener("click", function () {
                    InputPanel.toggle();
                });
            }
        },
        toggle: function () {
            var panel = $("input-panel");
            if (panel) panel.classList.toggle("collapsed");
        },
        collapse: function () {
            var panel = $("input-panel");
            if (panel) panel.classList.add("collapsed");
        },
        expand: function () {
            var panel = $("input-panel");
            if (panel) panel.classList.remove("collapsed");
        }
    };

    /* ===== Mobile Sidebar ===== */
    function initMobileSidebar() {
        var toggleBtn = $("mobile-menu-toggle");
        var sidebar = $("sidebar");
        var overlay = $("overlay");

        if (toggleBtn) {
            toggleBtn.addEventListener("click", function () {
                sidebar.classList.toggle("open");
                overlay.classList.toggle("active");
            });
        }
        if (overlay) {
            overlay.addEventListener("click", function () {
                sidebar.classList.remove("open");
                overlay.classList.remove("active");
            });
        }
    }

    /* ===== OCR Helpers ===== */
    function callOcr(file, model, onResult, onError) {
        var fd = new FormData();
        fd.append("image", file);
        fd.append("model", model);
                fetch("/ocr", { method: "POST", headers: AuthModule.getAuthHeader(), body: fd })
            .then(function (resp) {
                if (!resp.ok) {
                    return resp.json().catch(function () { return {}; }).then(function (e) {
                        throw new Error(e.error || "OCR HTTP " + resp.status);
                    });
                }
                return resp.json();
            })
            .then(onResult)
            .catch(onError);
    }

    function formatQuality(score) {
        return (score * 100).toFixed(0) + "%";
    }

    function showOcrLoading(resultEl, textEl, qualityEl, loadingEl) {
        resultEl.style.display = "";
        textEl.style.display = "none";
        qualityEl.textContent = "";
        loadingEl.style.display = "";
    }

    function showOcrSuccess(resultEl, textEl, qualityEl, loadingEl, r) {
        textEl.value = r.text || "";
        textEl.style.display = "";
        qualityEl.textContent = formatQuality(r.quality_score || 0);
        qualityEl.className = "quality-badge " +
            ((r.quality_score || 0) >= 0.7 ? "quality-badge-ok" : "quality-badge-warn");
        loadingEl.style.display = "none";

        var warnings = r.warnings || [];
        var ocrWarnings = warnings.filter(function (w) { return w.indexOf("ocr_model=") === 0; });
        if (ocrWarnings.length > 0) {
            var fallbackModel = "";
            var m = ocrWarnings[0].match(/ocr_model=(.+)/);
            if (m) fallbackModel = m[1].split(" (")[0];
            var notice = document.createElement("div");
            notice.className = "ocr-fallback-notice";
            notice.textContent = "\u63d0\u793a\uff1a\u4e3b\u6a21\u578b\u8bc6\u522b\u5931\u8d25\uff0c\u5df2\u81ea\u52a8\u5207\u6362\u81f3 " + fallbackModel + " \u6a21\u578b\u3002";
            var existing = resultEl.querySelector(".ocr-fallback-notice");
            if (existing) existing.remove();
            resultEl.insertBefore(notice, resultEl.firstChild);
        }
    }

    function showOcrError(resultEl, textEl, qualityEl, loadingEl) {
        textEl.value = "";
        textEl.style.display = "";
        qualityEl.textContent = "\u5931\u8d25";
        qualityEl.className = "quality-badge quality-badge-error";
        loadingEl.style.display = "none";
    }

    function hideOcr(resultEl, textEl, qualityEl, loadingEl) {
        resultEl.style.display = "none";
        textEl.value = "";
        textEl.style.display = "";
        qualityEl.textContent = "";
        loadingEl.style.display = "none";
    }

    /* ===== LaTeX Helpers ===== */
    function replaceLatexCommand(text, cmd, replacer) {
        var result = "";
        var i = 0;
        while (i < text.length) {
            var idx = text.indexOf(cmd, i);
            if (idx < 0) { result += text.substring(i); break; }
            result += text.substring(i, idx);
            var braceStart = text.indexOf("{", idx + cmd.length);
            if (braceStart < 0 || braceStart !== idx + cmd.length) { result += cmd; i = idx + cmd.length; continue; }
            var depth = 0;
            var end = braceStart;
            for (; end < text.length; end++) {
                if (text[end] === "{") depth++;
                if (text[end] === "}") depth--;
                if (depth === 0) break;
            }
            var arg = text.substring(braceStart + 1, end);
            result += replacer(arg);
            i = end + 1;
        }
        return result;
    }

    function wrapBareLatex(text) {
        if (/\$/.test(text)) return text;
        var latexCmds = /\\[a-zA-Z]+/;
        if (!latexCmds.test(text)) return text;
        var result = [];
        var i = 0;
        var n = text.length;
        var inMath = false;
        var mathStart = -1;
        var mathChars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789|,;!. +-*/=<>()[]^_{}\\";
        while (i < n) {
            if (text[i] === "\\" && i + 1 < n && /[a-zA-Z]/.test(text[i + 1])) {
                if (!inMath) { mathStart = i; inMath = true; }
                var j = i + 1;
                while (j < n && /[a-zA-Z]/.test(text[j])) j++;
                if (j < n && text[j] === "{") {
                    var d = 0;
                    for (var k = j; k < n; k++) {
                        if (text[k] === "{") d++;
                        if (text[k] === "}") d--;
                        if (d === 0) { j = k + 1; break; }
                    }
                }
                i = j;
            } else if (text[i] === "^" || text[i] === "_") {
                if (!inMath) { mathStart = i; inMath = true; }
                i++;
                if (i < n && text[i] === "{") {
                    var d2 = 0;
                    for (var k2 = i; k2 < n; k2++) {
                        if (text[k2] === "{") d2++;
                        if (text[k2] === "}") d2--;
                        if (d2 === 0) { i = k2 + 1; break; }
                    }
                } else if (i < n && /[a-zA-Z0-9]/.test(text[i])) {
                    i++;
                }
            } else if (text[i] === "{" || text[i] === "}") {
                if (!inMath) { mathStart = i; inMath = true; }
                i++;
            } else if (mathChars.indexOf(text[i]) >= 0) {
                if (!inMath) { mathStart = i; inMath = true; }
                i++;
            } else {
                if (inMath) {
                    var mathText = text.substring(mathStart, i).replace(/^\s+/, "").replace(/\s+$/, "");
                    result.push(mathText ? "$" + mathText + "$" : text.substring(mathStart, i));
                    inMath = false;
                    mathStart = -1;
                }
                result.push(text[i]);
                i++;
            }
        }
        if (inMath) {
            var mt = text.substring(mathStart, i).replace(/^\s+/, "").replace(/\s+$/, "");
            result.push(mt ? "$" + mt + "$" : text.substring(mathStart, i));
        }
        return result.join("");
    }

    function preprocessLatex(text) {
        var s = text.replace(/\\\\/g, "\\");
        s = replaceLatexCommand(s, "\\xlongequal", function (arg) { return "\\overset{" + arg + "}{=}"; });
        return s;
    }

    function renderOcrPreview(text, previewEl) {
        if (!window.katex) return;
        if (!text || !text.trim()) { previewEl.innerHTML = ""; previewEl.classList.add("is-empty"); return; }
        previewEl.classList.remove("is-empty");
        var processed = preprocessLatex(text);
        var wrapped = wrapBareLatex(processed);
        previewEl.textContent = wrapped;
        if (window.renderMathInElement) {
            renderMathInElement(previewEl, {
                delimiters: [
                    { left: "$$", right: "$$", display: true },
                    { left: "$", right: "$", display: false },
                    { left: "\\(", right: "\\)", display: false },
                    { left: "\\[", right: "\\]", display: true }
                ],
                throwOnError: false, strict: false, trust: true
            });
        }
    }

    var _previewTimers = {};
    function debounceRenderOcrPreview(id, text, previewEl) {
        if (_previewTimers[id]) clearTimeout(_previewTimers[id]);
        _previewTimers[id] = setTimeout(function () { renderOcrPreview(text, previewEl); }, 300);
    }

    function renderAllMath(container) {
        if (!window.renderMathInElement) return;
        var walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
        var textNodes = [];
        var node;
        while (node = walker.nextNode()) {
            if (node.nodeValue.indexOf("\\\\") >= 0) textNodes.push(node);
        }
        for (var i = 0; i < textNodes.length; i++) {
            textNodes[i].nodeValue = textNodes[i].nodeValue.replace(/\\\\/g, "\\");
        }
        renderMathInElement(container, {
            delimiters: [
                { left: "$$", right: "$$", display: true },
                { left: "$", right: "$", display: false },
                { left: "\\(", right: "\\)", display: false },
                { left: "\\[", right: "\\]", display: true }
            ],
            throwOnError: false, strict: false, trust: true
        });
    }

    /* ===== AuthModule ===== */
    var AuthModule = {
        TOKEN_KEY: "stem_tutor_token",
        USER_KEY: "stem_tutor_user",

        init: function () {
            var token = localStorage.getItem(this.TOKEN_KEY);
            if (token) {
                this._validateToken(token);
            } else {
                this.showAuthScreen();
            }
            this._bindEvents();
        },

        _validateToken: function (token) {
            var self = this;
            fetch("/api/auth/me", {
                headers: { "Authorization": "Bearer " + token }
            }).then(function (resp) {
                if (!resp.ok) throw new Error("invalid");
                return resp.json();
            }).then(function (user) {
                localStorage.setItem(self.USER_KEY, JSON.stringify(user));
                self.hideAuthScreen();
                self.updateUserDisplay(user);
                _onAuthReady();
            }).catch(function () {
                localStorage.removeItem(self.TOKEN_KEY);
                localStorage.removeItem(self.USER_KEY);
                self.showAuthScreen();
            });
        },

        showAuthScreen: function () {
            var el = $("auth-screen");
            if (el) el.style.display = "flex";
        },

        hideAuthScreen: function () {
            var el = $("auth-screen");
            if (el) el.style.display = "none";
        },

        updateUserDisplay: function (user) {
            var nameEl = $("user-display-name");
            var infoEl = $("user-info");
            var adminNav = $("nav-admin");
            if (nameEl && user) nameEl.textContent = user.username || "";
            if (infoEl) infoEl.style.display = "flex";
            if (adminNav) adminNav.style.display = user && user.is_admin ? "" : "none";
        },

        _bindEvents: function () {
            var self = this;

            var loginForm = $("auth-login-form");
            if (loginForm) loginForm.addEventListener("submit", function (e) { e.preventDefault(); self._login(); });

            var registerForm = $("auth-register-form");
            if (registerForm) registerForm.addEventListener("submit", function (e) { e.preventDefault(); self._register(); });

            var tabs = document.querySelectorAll(".auth-tab");
            tabs.forEach(function (tab) {
                tab.addEventListener("click", function () {
                    tabs.forEach(function (t) { t.classList.remove("active"); });
                    this.classList.add("active");
                    var target = this.getAttribute("data-auth-tab");
                    var loginForm = $("auth-login-form");
                    var registerForm = $("auth-register-form");
                    if (loginForm) loginForm.style.display = target === "login" ? "" : "none";
                    if (registerForm) registerForm.style.display = target === "register" ? "" : "none";
                    var loginErr = $("auth-login-error");
                    var regErr = $("auth-register-error");
                    if (loginErr) loginErr.style.display = "none";
                    if (regErr) regErr.style.display = "none";
                });
            });

            var logoutBtn = $("btn-logout");
            if (logoutBtn) logoutBtn.addEventListener("click", function () { self.logout(); });
        },

        _login: function () {
            var self = this;
            var username = ($("auth-username").value || "").trim();
            var password = $("auth-password").value || "";
            var errorEl = $("auth-login-error");

            if (!username || !password) {
                if (errorEl) { errorEl.textContent = "\u8bf7\u8f93\u5165\u7528\u6237\u540d\u548c\u5bc6\u7801"; errorEl.style.display = ""; }
                return;
            }

            fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username, password: password })
            }).then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) throw new Error(data.detail || "\u767b\u5f55\u5931\u8d25");
                    return data;
                });
            }).then(function (data) {
                localStorage.setItem(self.TOKEN_KEY, data.access_token);
                localStorage.setItem(self.USER_KEY, JSON.stringify(data.user));
                self.hideAuthScreen();
                self.updateUserDisplay(data.user);
                self._syncLocalData();
                _onAuthReady();
            }).catch(function (err) {
                if (errorEl) { errorEl.textContent = err.message || "\u767b\u5f55\u5931\u8d25"; errorEl.style.display = ""; }
            });
        },

        _register: function () {
            var self = this;
            var username = ($("reg-username").value || "").trim();
            var password = $("reg-password").value || "";
            var errorEl = $("auth-register-error");

            if (username.length < 2 || username.length > 32) {
                if (errorEl) { errorEl.textContent = "\u7528\u6237\u540d\u9700\u8981 2-32 \u4e2a\u5b57\u7b26"; errorEl.style.display = ""; }
                return;
            }
            if (password.length < 4) {
                if (errorEl) { errorEl.textContent = "\u5bc6\u7801\u81f3\u5c11 4 \u4f4d"; errorEl.style.display = ""; }
                return;
            }

            fetch("/api/auth/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username, password: password })
            }).then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) throw new Error(data.detail || "\u6ce8\u518c\u5931\u8d25");
                    return data;
                });
            }).then(function (data) {
                if (data.status === "pending") {
                    if (errorEl) {
                        errorEl.style.color = "var(--success, #4caf50)";
                        errorEl.textContent = "注册成功，请等待管理员审批后登录";
                        errorEl.style.display = "";
                    }
                    var form = $("auth-register-form");
                    if (form) form.reset();
                    return;
                }
                localStorage.setItem(self.TOKEN_KEY, data.access_token);
                localStorage.setItem(self.USER_KEY, JSON.stringify(data.user));
                self.hideAuthScreen();
                self.updateUserDisplay(data.user);
                self._syncLocalData();
                _onAuthReady();
            }).catch(function (err) {
                if (errorEl) { errorEl.style.color = ""; errorEl.textContent = err.message || "注册失败"; errorEl.style.display = ""; }
            });
        },

        logout: function () {
            localStorage.removeItem(this.TOKEN_KEY);
            localStorage.removeItem(this.USER_KEY);
            var infoEl = $("user-info");
            var adminNav = $("nav-admin");
            if (infoEl) infoEl.style.display = "none";
            if (adminNav) adminNav.style.display = "none";
            this.showAuthScreen();
        },

        getAuthHeader: function () {
            var token = localStorage.getItem(this.TOKEN_KEY);
            if (token) return { "Authorization": "Bearer " + token };
            return {};
        },

        _syncLocalData: function () {
            var masteryRaw = localStorage.getItem("stem_tutor_mastery");
            if (masteryRaw) {
                try {
                    var mastery = JSON.parse(masteryRaw);
                    fetch("/api/user/mastery", {
                        method: "POST",
                        headers: Object.assign({ "Content-Type": "application/json" }, this.getAuthHeader()),
                        body: JSON.stringify(mastery)
                    }).catch(function () {});
                } catch (e) {}
            }
            var settingsRaw = localStorage.getItem("stem_tutor_settings");
            if (settingsRaw) {
                try {
                    var settings = JSON.parse(settingsRaw);
                    fetch("/api/user/settings", {
                        method: "POST",
                        headers: Object.assign({ "Content-Type": "application/json" }, this.getAuthHeader()),
                        body: JSON.stringify(settings)
                    }).catch(function () {});
                } catch (e) {}
            }
        }
    };

    /* ===== Main Init ===== */
    var _authReady = false;

    var AdminPanel = {
        _loaded: false,
        _users: [],
        _pendingDelete: null,
        _detailUserId: null,
        _detailData: null,
        _detailRunsPage: 1,
        _detailTabsBound: false,

        init: function () {
            var self = this;
            var refreshBtn = $("admin-refresh-btn");
            if (refreshBtn && !refreshBtn._bound) {
                refreshBtn._bound = true;
                refreshBtn.addEventListener("click", function () { self.load(); });
            }
        },

        navigateToUser: function (userId) {
            location.hash = "admin/user/" + userId;
        },

        load: function () {
            var self = this;
            this.init();
            var page = $("page-admin");
            var loading = $("admin-loading");
            var empty = $("admin-empty");
            var content = $("admin-content");
            if (page) page.style.display = "";
            if (loading) loading.style.display = "";
            if (empty) empty.style.display = "none";
            if (content) content.style.display = "none";

            Promise.all([
                fetch("/api/admin/stats", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (data) {
                        if (!r.ok) throw new Error(data.detail || data.error || "加载失败");
                        return data;
                    });
                }),
                fetch("/api/admin/users", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (data) {
                        if (!r.ok) throw new Error(data.detail || data.error || "加载失败");
                        return data;
                    });
                }),
                fetch("/api/admin/pending-users", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (data) { if (!r.ok) throw new Error(data.detail || data.error || "加载失败"); return data; });
                })
            ]).then(function (results) {
                var stats = results[0] || {};
                var users = results[1] || [];
                var pending = results[2] || [];
                self._users = users;
                self.renderStats(stats, users);
                self.renderUsers(users);
                self.renderPendingUsers(pending);
                self.renderExport();
                if (loading) loading.style.display = "none";
                if (content) content.style.display = "";
                if (!users.length && empty) empty.style.display = "";
            }).catch(function (err) {
                if (loading) loading.style.display = "none";
                if (empty) {
                    empty.style.display = "";
                    empty.innerHTML = '<p>加载失败：' + esc((err && err.message) || "未知错误") + '</p>';
                }
            });
        },

        loadUserDetail: function (userId) {
            var self = this;
            this._detailUserId = userId;
            this._detailData = null;
            this._detailRunsPage = 1;

            $("admin-user-list").style.display = "none";
            $("admin-user-detail").style.display = "";
            $("admin-detail-loading").style.display = "";
            $("admin-detail-runs").style.display = "none";
            $("admin-detail-reports").style.display = "none";
            $("admin-detail-chats").style.display = "none";
            $("admin-detail-settings").style.display = "none";
            $("admin-detail-info").style.display = "none";
            document.querySelectorAll(".admin-detail-tab").forEach(function (t) { t.style.display = "none"; });

            Promise.all([
                fetch("/api/admin/users/" + userId, { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || d.error || "加载失败"); return d; });
                }),
                fetch("/api/admin/users/" + userId + "/runs?per_page=20", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || d.error || "加载失败"); return d; });
                }),
                fetch("/api/admin/users/" + userId + "/reports", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || d.error || "加载失败"); return d; });
                }),
                fetch("/api/admin/users/" + userId + "/chats", { headers: AuthModule.getAuthHeader() }).then(function (r) {
                    return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || d.error || "加载失败"); return d; });
                })
            ]).then(function (results) {
                self._detailData = {
                    info: results[0],
                    runs: results[1],
                    reports: results[2],
                    chats: results[3]
                };
                self._renderUserDetail(self._detailData);
            }).catch(function (err) {
                $("admin-detail-loading").style.display = "none";
                $("admin-detail-runs").style.display = "";
                $("admin-detail-runs").innerHTML = '<div class="admin-empty-hint">加载失败：' + esc(err.message || "未知错误") + '</div>';
            });
        },

        _renderUserDetail: function (data) {
            var user = data.info.user;
            $("admin-detail-username").textContent = user.username + " - 用户详情";
            $("admin-detail-id").textContent = user.id;
            $("admin-detail-role").textContent = user.is_admin ? "管理员" : "普通用户";
            $("admin-detail-created").textContent = formatTimestamp(user.created_at);
            $("admin-detail-info").style.display = "";
            $("admin-detail-loading").style.display = "none";
            document.querySelectorAll(".admin-detail-tab").forEach(function (t) { t.style.display = ""; });

            this._renderDetailRuns(data.runs);
            this._renderDetailReports(data.reports);
            this._renderDetailChats(data.chats);
            this._renderDetailSettings(data.info.settings, data.info.mastery);

            this._bindDetailTabs();
            $("admin-detail-runs").style.display = "";
            $("admin-detail-runs").classList.add("active");
        },

        _renderDetailRuns: function (runsData) {
            var container = $("admin-runs-list");
            var runs = runsData.runs || [];
            if (!runs.length) {
                container.innerHTML = '<div class="admin-empty-hint">该用户暂无运行记录</div>';
                return;
            }
            var html = "";
            runs.forEach(function (run) {
                var badge = getStatusBadge(run.user_status || run.status);
                html += '<div class="admin-run-card" data-run-id="' + esc(run.run_id) + '">';
                html += '<div class="admin-run-card-header">' + esc(run.problem_preview || "无题目") + '</div>';
                html += '<div class="admin-run-card-meta">';
                if (run.subject_display) html += '<span>📚 ' + esc(run.subject_display) + '</span>';
                if (run.timestamp) html += '<span>🕒 ' + esc(formatTimestamp(run.timestamp)) + '</span>';
                html += '<span><span class="status-badge ' + badge.cls + '">' + badge.text + '</span></span>';
                if (run.duration_seconds) html += '<span>⏱ ' + formatDuration(run.duration_seconds) + '</span>';
                html += '</div>';
                html += '</div>';
            });
            container.innerHTML = html;

            var self = this;
            container.querySelectorAll(".admin-run-card").forEach(function (card) {
                card.addEventListener("click", function () {
                    self._showRunDetail(card.getAttribute("data-run-id"));
                });
            });

            this._renderDetailRunsPagination(runsData);
        },

        _renderDetailRunsPagination: function (runsData) {
            var paginationEl = $("admin-runs-pagination");
            if (!paginationEl) return;
            var total = runsData.total || 0;
            var page = runsData.page || 1;
            var perPage = runsData.per_page || 20;
            var totalPages = Math.ceil(total / perPage);

            if (totalPages <= 1) {
                paginationEl.style.display = "none";
                return;
            }

            var html = '<div class="pagination-info"><span>第 ' + ((page - 1) * perPage + 1) + '-' + Math.min(page * perPage, total) + ' 条，共 ' + total + ' 条</span></div>';
            html += '<div class="pagination-controls">';
            html += '<button type="button" class="btn-secondary btn-sm" data-page="prev"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>';
            html += '<span class="pagination-pages">' + page + ' / ' + totalPages + '</span>';
            html += '<button type="button" class="btn-secondary btn-sm" data-page="next"' + (page >= totalPages ? ' disabled' : '') + '>下一页</button>';
            html += '</div>';
            paginationEl.innerHTML = html;
            paginationEl.style.display = "";

            var self = this;
            paginationEl.querySelectorAll("button[data-page]").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    var action = btn.getAttribute("data-page");
                    var newPage = self._detailRunsPage;
                    if (action === "prev") newPage = Math.max(1, page - 1);
                    if (action === "next") newPage = Math.min(totalPages, page + 1);
                    if (newPage !== self._detailRunsPage) {
                        self._detailRunsPage = newPage;
                        self._loadMoreRuns();
                    }
                });
            });
        },

        _loadMoreRuns: function () {
            var self = this;
            var userId = this._detailUserId;
            var page = this._detailRunsPage;
            fetch("/api/admin/users/" + userId + "/runs?page=" + page + "&per_page=20", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    self._renderDetailRuns(data);
                });
        },

        _renderDetailReports: function (reports) {
            var container = $("admin-reports-list");
            if (!reports || !reports.length) {
                container.innerHTML = '<div class="admin-empty-hint">该用户暂无学习报告</div>';
                return;
            }
            var html = "";
            reports.forEach(function (rep) {
                var title = (rep.data && rep.data.title) || rep.title || "无标题报告";
                var createdAt = rep.created_at || "";
                html += '<div class="admin-report-card">';
                html += '<div class="admin-report-card-title">' + esc(title) + '</div>';
                html += '<div class="admin-report-card-meta">生成时间：' + esc(formatTimestamp(createdAt)) + '</div>';
                html += '</div>';
            });
            container.innerHTML = html;
        },

        _renderDetailChats: function (chats) {
            var container = $("admin-chats-list");
            if (!chats || !chats.length) {
                container.innerHTML = '<div class="admin-empty-hint">该用户暂无聊天记录</div>';
                return;
            }
            var html = "";
            chats.forEach(function (chat) {
                var messages = chat.messages || [];
                html += '<div class="admin-chat-card" data-run-id="' + esc(chat.run_id) + '">';
                html += '<div class="admin-chat-card-header">';
                html += '<span class="admin-chat-run-id">Run: ' + esc(chat.run_id) + '</span>';
                html += '<span class="admin-chat-count">' + messages.length + ' 条消息</span>';
                html += '</div>';
                if (messages.length) {
                    html += '<div class="admin-chat-messages">';
                    var displayMessages = messages.slice(-5);
                    displayMessages.forEach(function (msg) {
                        var role = msg.role || "user";
                        var content = msg.content || "";
                        var roleLabel = role === "user" ? "用户" : "AI";
                        html += '<div class="admin-chat-bubble ' + role + '">';
                        html += '<span class="chat-role-label">' + esc(roleLabel) + '</span>';
                        html += esc(content.length > 200 ? content.substring(0, 200) + "..." : content);
                        html += '</div>';
                    });
                    if (messages.length > 5) {
                        html += '<div class="admin-empty-hint" style="padding:8px;">... 还有 ' + (messages.length - 5) + ' 条消息</div>';
                    }
                    html += '</div>';
                }
                html += '</div>';
            });
            container.innerHTML = html;
        },

        _renderDetailSettings: function (settings, mastery) {
            var settingsEl = $("admin-user-settings-content");
            var masteryEl = $("admin-user-mastery-content");

            if (settings && Object.keys(settings).length > 0) {
                var sHtml = '<table class="admin-kv-table">';
                Object.keys(settings).forEach(function (key) {
                    sHtml += '<tr><td>' + esc(key) + '</td><td>' + esc(JSON.stringify(settings[key], null, 2)) + '</td></tr>';
                });
                sHtml += '</table>';
                settingsEl.innerHTML = sHtml;
            } else {
                settingsEl.innerHTML = '<div class="admin-empty-hint">暂无设置数据</div>';
            }

            if (mastery && Object.keys(mastery).length > 0 && (Object.keys(mastery.errors || {}).length > 0 || (mastery.practice_history || []).length > 0)) {
                var mHtml = '';
                if (mastery.errors && Object.keys(mastery.errors).length > 0) {
                    mHtml += '<h5 style="margin:0 0 8px;font-size:0.92rem;">错误分布</h5>';
                    mHtml += '<table class="admin-kv-table">';
                    Object.keys(mastery.errors).forEach(function (key) {
                        mHtml += '<tr><td>' + esc(key) + '</td><td>' + esc(String(mastery.errors[key])) + '</td></tr>';
                    });
                    mHtml += '</table>';
                }
                if (mastery.practice_history && mastery.practice_history.length > 0) {
                    mHtml += '<h5 style="margin:12px 0 8px;font-size:0.92rem;">练习历史 (' + mastery.practice_history.length + ' 条)</h5>';
                    mHtml += '<table class="admin-kv-table">';
                    var showItems = mastery.practice_history.slice(-10);
                    showItems.forEach(function (item) {
                        mHtml += '<tr><td colspan="2">' + esc(JSON.stringify(item)) + '</td></tr>';
                    });
                    if (mastery.practice_history.length > 10) {
                        mHtml += '<tr><td colspan="2" class="admin-empty-hint">... 还有 ' + (mastery.practice_history.length - 10) + ' 条</td></tr>';
                    }
                    mHtml += '</table>';
                }
                masteryEl.innerHTML = mHtml;
            } else {
                masteryEl.innerHTML = '<div class="admin-empty-hint">暂无掌握度数据</div>';
            }
        },

        _bindDetailTabs: function () {
            if (this._detailTabsBound) return;
            this._detailTabsBound = true;
            var tabs = document.querySelectorAll(".admin-detail-tab");
            var panels = ["admin-detail-runs", "admin-detail-reports", "admin-detail-chats", "admin-detail-settings"];
            for (var i = 0; i < tabs.length; i++) {
                (function (tab) {
                    tab.addEventListener("click", function () {
                        for (var j = 0; j < tabs.length; j++) tabs[j].classList.remove("active");
                        for (var k = 0; k < panels.length; k++) $(panels[k]).classList.remove("active");
                        tab.classList.add("active");
                        var target = $("admin-detail-" + tab.getAttribute("data-tab"));
                        if (target) target.classList.add("active");
                    });
                })(tabs[i]);
            }

            var backBtn = $("admin-detail-back");
            if (backBtn && !backBtn._bound) {
                backBtn._bound = true;
                backBtn.addEventListener("click", function () {
                    AdminPanel.showList();
                });
            }
        },

        showList: function () {
            $("admin-user-detail").style.display = "none";
            $("admin-user-list").style.display = "";
            $("admin-run-detail-modal").style.display = "none";
            location.hash = "admin";
            this._detailUserId = null;
            this._detailData = null;
        },

        _showRunDetail: function (runId) {
            var self = this;
            var userId = this._detailUserId;
            if (!userId) return;

            var modal = $("admin-run-detail-modal");
            var body = $("admin-run-detail-body");
            modal.style.display = "";
            body.innerHTML = '<div class="admin-detail-loading">正在加载运行详情...</div>';

            fetch("/api/admin/users/" + userId + "/run/" + runId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) {
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.json();
                })
                .then(function (data) {
                    self._renderRunDetailBody(data);
                })
                .catch(function (err) {
                    body.innerHTML = '<div class="alert alert-error">加载失败: ' + esc(err.message) + '</div>';
                });

            var closeBtn = $("admin-run-detail-close");
            if (closeBtn && !closeBtn._bound) {
                closeBtn._bound = true;
                closeBtn.addEventListener("click", function () {
                    $("admin-run-detail-modal").style.display = "none";
                });
            }
            modal.onclick = function (e) {
                if (e.target === modal) modal.style.display = "none";
            };
        },

        _renderRunDetailBody: function (data) {
            var body = $("admin-run-detail-body");
            var meta = data.run_meta || {};
            var html = '';

            html += '<div class="logs-meta-grid">';
            html += '<span><b>Run ID</b>: ' + esc(meta.run_id || "-") + '</span>';
            html += '<span><b>模式</b>: ' + esc(meta.mode || "-") + '</span>';
            html += '<span><b>模型</b>: ' + esc(meta.model || "-") + '</span>';
            html += '<span><b>状态</b>: ' + esc(data.user_status || data.status || "-") + '</span>';
            if (meta.started_at) html += '<span><b>开始</b>: ' + esc(formatTimestamp(meta.started_at)) + '</span>';
            if (meta.completed_at) html += '<span><b>完成</b>: ' + esc(formatTimestamp(meta.completed_at)) + '</span>';
            if (meta.subject_id) html += '<span><b>学科</b>: ' + esc(meta.subject_id) + '</span>';
            if (data.fail_reason) html += '<span><b>失败原因</b>: ' + esc(data.fail_reason) + '</span>';
            html += '</div>';

            var raw = data.raw_output || {};

            if (raw.problem_input) {
                var problemText = "";
                if (typeof raw.problem_input === "object" && raw.problem_input.problem_text) {
                    problemText = raw.problem_input.problem_text;
                } else if (typeof raw.problem_input === "string") {
                    problemText = raw.problem_input;
                }
                if (problemText) {
                    html += '<div class="admin-section" style="margin-top:12px;">';
                    html += '<h4>题目</h4>';
                    html += '<div style="padding:8px;">' + esc(problemText) + '</div>';
                    html += '</div>';
                }
            }

            var steps = raw.normalized_steps || [];
            if (steps.length) {
                html += '<div class="admin-section" style="margin-top:12px;">';
                html += '<h4>解题步骤 (' + steps.length + ' 步)</h4>';
                html += '<table class="admin-table" style="margin-top:8px;">';
                html += '<thead><tr><th>步骤</th><th>内容</th><th>标签</th><th>置信度</th></tr></thead>';
                html += '<tbody>';
                steps.forEach(function (s) {
                    var label = s.label || "unverified";
                    var labelInfo = LABEL_MAP[label] || LABEL_MAP.unverified;
                    html += '<tr>';
                    html += '<td>' + esc(s.step_id || "?") + '</td>';
                    html += '<td style="max-width:400px;word-break:break-word;">' + esc(s.raw_text || s.normalized_text || "") + '</td>';
                    html += '<td><span class="label-badge ' + labelInfo.cls + '">' + labelInfo.text + '</span></td>';
                    html += '<td>' + (s.confidence != null ? (s.confidence * 100).toFixed(0) + "%" : "--") + '</td>';
                    html += '</tr>';
                    if (s.evidence && label !== "correct") {
                        html += '<tr><td></td><td colspan="3" style="color:var(--text-secondary);font-size:0.85rem;">证据：' + esc(s.evidence) + '</td></tr>';
                    }
                });
                html += '</tbody></table>';
                html += '</div>';
            }

            var diagnoses = raw.diagnosis_results || [];
            if (diagnoses.length) {
                html += '<div class="admin-section" style="margin-top:12px;">';
                html += '<h4>错误诊断</h4>';
                diagnoses.forEach(function (d) {
                    html += '<div class="diagnosis-card">';
                    html += '<p class="diagnosis-step">步骤 ' + esc(d.step_id || "?") + '</p>';
                    html += '<span class="diagnosis-code">' + esc(d.error_code || "?") + '</span> ' + esc(d.short_desc || "");
                    if (d.category) html += '<p><span class="field-label">类别：</span>' + esc(d.category) + '</p>';
                    if (d.root_cause_hypothesis) html += '<p><span class="field-label">根因：</span>' + esc(d.root_cause_hypothesis) + '</p>';
                    html += '</div>';
                });
                html += '</div>';
            }

            if (raw.final_feedback) {
                var fb = raw.final_feedback;
                html += '<div class="admin-section" style="margin-top:12px;">';
                html += '<h4>学习反馈</h4>';
                html += '<div style="padding:8px;">';
                if (fb.concise_summary) html += '<p>' + esc(fb.concise_summary) + '</p>';
                if (fb.next_action) html += '<p><span class="field-label">建议：</span>' + esc(fb.next_action) + '</p>';
                html += '</div>';
                html += '</div>';
            }

            html += '<details class="card" style="margin-top:12px;">';
            html += '<summary>原始 JSON</summary>';
            html += '<pre style="max-height:400px;overflow:auto;font-size:0.82rem;">' + esc(JSON.stringify(raw, null, 2)) + '</pre>';
            html += '</details>';

            body.innerHTML = html;

            if (window.renderMathInElement) {
                renderMathInElement(body, {
                    delimiters: [
                        { left: "$$", right: "$$", display: true },
                        { left: "$", right: "$", display: false },
                        { left: "\\(", right: "\\)", display: false },
                        { left: "\\[", right: "\\]", display: true }
                    ],
                    throwOnError: false, strict: false, trust: true
                });
            }
        },

        renderStats: function (stats, users) {
            var totalUsers = users.length || stats.user_count || 0;
            var adminCount = (users || []).filter(function (u) { return u.is_admin; }).length;
            var runCount = stats.run_count || 0;
            var usersEl = $("admin-stat-users");
            var runsEl = $("admin-stat-runs");
            var adminsEl = $("admin-stat-admins");
            if (usersEl) usersEl.textContent = totalUsers;
            if (runsEl) runsEl.textContent = runCount;
            if (adminsEl) adminsEl.textContent = adminCount;
            var pendingEl = $("admin-stat-pending");
            if (pendingEl) pendingEl.textContent = stats.pending_count || 0;
        },

        renderUsers: function (users) {
            var body = $("admin-users-body");
            if (!body) return;
            if (!users || !users.length) {
                body.innerHTML = '';
                return;
            }
            var html = '';
            users.forEach(function (user) {
                var role = user.is_admin ? '管理员' : '用户';
                var badge = user.is_admin ? 'status-complete' : 'status-running';
                html += '<tr>';
                html += '<td>' + esc(String(user.id)) + '</td>';
                html += '<td>' + esc(user.username || '') + '</td>';
                html += '<td><span class="status-badge ' + badge + '">' + role + '</span></td>';
                var statusText = user.status === 'pending' ? '待审批' : (user.status === 'active' ? '正常' : user.status);
                var statusBadge = user.status === 'pending' ? 'status-running' : 'status-complete';
                html += '<td><span class="status-badge ' + statusBadge + '">' + statusText + '</span></td>';
                html += '<td>' + esc(formatTimestamp(user.created_at)) + '</td>';
                html += '<td>';
                html += '<button type="button" class="btn-secondary btn-sm admin-view-btn" data-user-id="' + esc(String(user.id)) + '">查看</button>';
                html += '<button type="button" class="btn-danger btn-sm admin-delete-btn" data-user-id="' + esc(String(user.id)) + '" data-username="' + esc(user.username || '') + '">删除</button>';
                html += '</td>';
                html += '</tr>';
            });
            body.innerHTML = html;
            var self = this;
            body.querySelectorAll('.admin-delete-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    self._openDeleteDialog({
                        id: parseInt(this.getAttribute('data-user-id'), 10),
                        username: this.getAttribute('data-username') || ''
                    });
                });
            });
            body.querySelectorAll('.admin-view-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var userId = parseInt(this.getAttribute('data-user-id'), 10);
                    if (!isNaN(userId)) self.navigateToUser(userId);
                });
            });
        },

        renderPendingUsers: function (pending) {
            var section = $("admin-pending-section");
            var body = $("admin-pending-body");
            if (!section || !body) return;
            if (!pending || !pending.length) {
                section.style.display = "none";
                return;
            }
            section.style.display = "";
            var html = '';
            pending.forEach(function (user) {
                html += '<tr>';
                html += '<td>' + esc(String(user.id)) + '</td>';
                html += '<td>' + esc(user.username || '') + '</td>';
                html += '<td>' + esc(formatTimestamp(user.created_at)) + '</td>';
                html += '<td>';
                html += '<button type="button" class="btn-primary btn-sm admin-approve-btn" data-user-id="' + esc(String(user.id)) + '">通过</button>';
                html += '<button type="button" class="btn-danger btn-sm admin-reject-btn" data-user-id="' + esc(String(user.id)) + '" data-username="' + esc(user.username || '') + '">拒绝</button>';
                html += '</td>';
                html += '</tr>';
            });
            body.innerHTML = html;
            var self = this;
            body.querySelectorAll('.admin-approve-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var uid = parseInt(this.getAttribute('data-user-id'), 10);
                    if (!isNaN(uid)) self._approveUser(uid);
                });
            });
            body.querySelectorAll('.admin-reject-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var uid = parseInt(this.getAttribute('data-user-id'), 10);
                    var uname = this.getAttribute('data-username') || '';
                    if (!isNaN(uid)) self._rejectUser(uid, uname);
                });
            });
            this._bindApproveAll();
            this._bindRejectAll();
        },

        _approveUser: function (userId) {
            var self = this;
            fetch("/api/admin/users/" + userId + "/approve", {
                method: "POST",
                headers: AuthModule.getAuthHeader()
            }).then(function (r) {
                return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || "操作失败"); return d; });
            }).then(function () {
                self.load();
            }).catch(function (err) {
                alert("审批失败：" + (err.message || "未知错误"));
            });
        },

        _rejectUser: function (userId, username) {
            if (!confirm("确定拒绝用户 " + username + " 的注册申请？（该用户名可被重新注册）")) return;
            var self = this;
            fetch("/api/admin/users/" + userId + "/reject", {
                method: "POST",
                headers: AuthModule.getAuthHeader()
            }).then(function (r) {
                return r.json().then(function (d) { if (!r.ok) throw new Error(d.detail || "操作失败"); return d; });
            }).then(function () {
                self.load();
            }).catch(function (err) {
                alert("拒绝失败：" + (err.message || "未知错误"));
            });
        },

        _bindApproveAll: function () {
            var btn = $("admin-approve-all-btn");
            if (!btn || btn._bound) return;
            btn._bound = true;
            var self = this;
            btn.addEventListener("click", function () {
                var body = $("admin-pending-body");
                var count = body ? body.querySelectorAll("tr").length : 0;
                if (count === 0) return;
                if (!confirm("确定通过所有 " + count + " 个待审批用户？")) return;
                var rows = body.querySelectorAll(".admin-approve-btn");
                var promises = [];
                rows.forEach(function (r) {
                    var uid = parseInt(r.getAttribute("data-user-id"), 10);
                    if (!isNaN(uid)) {
                        promises.push(
                            fetch("/api/admin/users/" + uid + "/approve", {
                                method: "POST",
                                headers: AuthModule.getAuthHeader()
                            }).then(function (r2) { return r2.json(); })
                        );
                    }
                });
                Promise.all(promises).then(function () { self.load(); });
            });
        },

        _bindRejectAll: function () {
            var btn = $("admin-reject-all-btn");
            if (!btn || btn._bound) return;
            btn._bound = true;
            var self = this;
            btn.addEventListener("click", function () {
                var body = $("admin-pending-body");
                var count = body ? body.querySelectorAll("tr").length : 0;
                if (count === 0) return;
                if (!confirm("确定拒绝所有 " + count + " 个待审批用户？用户名可被重新注册。")) return;
                var rows = body.querySelectorAll(".admin-reject-btn");
                var promises = [];
                rows.forEach(function (r) {
                    var uid = parseInt(r.getAttribute("data-user-id"), 10);
                    if (!isNaN(uid)) {
                        promises.push(
                            fetch("/api/admin/users/" + uid + "/reject", {
                                method: "POST",
                                headers: AuthModule.getAuthHeader()
                            }).then(function (r2) { return r2.json(); })
                        );
                    }
                });
                Promise.all(promises).then(function () { self.load(); });
            });
        },

        _openDeleteDialog: function (user) {
            var self = this;
            this._pendingDelete = user;
            var overlay = $("confirm-overlay");
            var title = $("confirm-title");
            var msg = $("confirm-message");
            var ok = $("confirm-ok");
            var cancel = $("confirm-cancel");
            if (ok) ok.onclick = null;
            if (cancel) cancel.onclick = null;
            if (overlay) overlay.onclick = null;
            if (overlay) overlay.style.display = "flex";
            if (title) title.textContent = "删除用户";
            if (msg) {
                msg.innerHTML = '<div class="admin-confirm-body"><p>确定删除用户 <b>' + esc(user.username || '') + '</b> 吗？</p><label class="admin-cascade-toggle"><input type="checkbox" id="admin-cascade-checkbox" checked> 同时删除该用户的所有运行记录、聊天记录、报告、设置和 mastery 数据</label><p class="admin-confirm-note">关闭级联后只删除用户账号本身，关联数据会保留。</p></div>';
            }
            if (ok) ok.textContent = "确认删除";
            if (ok) ok.onclick = function () { if (self._pendingDelete) self._confirmDelete(); };
            if (cancel) cancel.onclick = function () { self._hideDeleteDialog(); };
            if (overlay) overlay.onclick = function (e) { if (e && e.target === overlay) self._hideDeleteDialog(); };
        },

        _hideDeleteDialog: function () {
            this._pendingDelete = null;
            var overlay = $("confirm-overlay");
            var ok = $("confirm-ok");
            var cancel = $("confirm-cancel");
            if (overlay) overlay.style.display = "none";
            var msg = $("confirm-message");
            if (msg) msg.innerHTML = '';
            if (ok) ok.textContent = "确认删除";
            if (ok) ok.onclick = null;
            if (cancel) cancel.onclick = null;
            if (overlay) overlay.onclick = null;
        },

        _confirmDelete: function () {
            var self = this;
            if (!this._pendingDelete) return;
            var user = this._pendingDelete;
            var cb = $("admin-cascade-checkbox");
            var cascade = cb ? cb.checked : true;
            fetch("/api/admin/users/" + user.id + "?cascade=" + (cascade ? "true" : "false"), {
                method: "DELETE",
                headers: AuthModule.getAuthHeader()
            }).then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data.detail || data.error || "删除失败");
                    return data;
                });
            }).then(function () {
                self._hideDeleteDialog();
                self.load();
            }).catch(function (err) {
                var msg = $("confirm-message");
                if (msg) msg.innerHTML = '<div class="alert alert-error">' + esc(err.message || '删除失败') + '</div>';
            });
        },

        renderExport: function () {
            var section = $("admin-export-section");
            if (section) section.style.display = "";
            var userSelect = $("export-user");
            if (userSelect) {
                userSelect.innerHTML = '<option value="">全部用户</option>';
                (this._users || []).forEach(function (u) {
                    var opt = document.createElement("option");
                    opt.value = u.id;
                    opt.textContent = u.username + " (ID: " + u.id + ")";
                    userSelect.appendChild(opt);
                });
            }
            var btn = $("btn-export-problems");
            if (!btn || btn._bound) return;
            btn._bound = true;
            var self = this;
            btn.addEventListener("click", function () { self._exportProblems(); });
        },

        _exportProblems: function () {
            var btn = $("btn-export-problems");
            if (!btn || btn.disabled) return;
            btn.disabled = true;
            btn.textContent = "导出中...";
            var subjectEl = $("export-subject");
            var userEl = $("export-user");
            var sinceEl = $("export-since-days");
            var statusEl = $("export-status");
            if (!subjectEl || !userEl || !sinceEl || !statusEl) {
                btn.disabled = false;
                btn.textContent = "导出数据";
                return;
            }
            var params = new URLSearchParams();
            var userId = userEl.value;
            var subject = subjectEl.value;
            var sinceDays = sinceEl.value;
            var status = statusEl.value;
            if (userId) params.set("user_id", userId);
            if (subject) params.set("subject", subject);
            if (sinceDays) params.set("since_days", sinceDays);
            if (status) params.set("status", status);
            fetch("/api/admin/export/problems?" + params.toString(), {
                headers: AuthModule.getAuthHeader()
            }).then(function (r) {
                if (!r.ok) return r.json().then(function (d) {
                    throw new Error(d.detail || d.error || "导出失败");
                });
                return r.blob();
            }).then(function (blob) {
                var url = URL.createObjectURL(blob);
                var a = document.createElement("a");
                a.href = url;
                a.download = "problems_export_" + new Date().toISOString().slice(0, 10) + ".jsonl";
                a.click();
                setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
                ExportModule._showToast("数据导出成功");
            }).catch(function (err) {
                ExportModule._showToast("导出失败: " + (err.message || "未知错误"));
            }).then(function () {
                btn.disabled = false;
                btn.textContent = "导出数据";
            });
        }
    };

    window.AdminPanel = AdminPanel;

    function openCropModal(file, callback) {
        cropCallback = callback;
        var reader = new FileReader();
        reader.onload = function (e) {
            var dataUrl = e.target.result;
            $("crop-image").onload = function () {
                $("crop-modal").style.display = "flex";
                if (cropperInstance) cropperInstance.destroy();
                cropperInstance = new Cropper($("crop-image"), {
                    viewMode: 1, dragMode: "move", autoCropArea: 1.0, responsive: true,
                });
            };
            $("crop-image").src = dataUrl;
        };
        reader.readAsDataURL(file);
    }

    function closeCropModal() {
        if (cropperInstance) { cropperInstance.destroy(); cropperInstance = null; }
        $("crop-modal").style.display = "none";
        $("crop-image").src = "";
        cropCallback = null;
    }

    function confirmCrop() {
        if (!cropperInstance || !cropCallback) return;
        cropperInstance.getCroppedCanvas({ maxWidth: 2048, maxHeight: 2048, fillColor: "#fff" })
            .toBlob(function (blob) {
                var croppedFile = new File([blob], "cropped.jpg", { type: "image/jpeg" });
                cropCallback(croppedFile);
                closeCropModal();
            }, "image/jpeg", 0.92);
    }

    function _onAuthReady() {
        if (_authReady) return;
        _authReady = true;
        AppRouter.init();
        SettingsModule.init();
        InputPanel.init();
        initMobileSidebar();
        OnboardingModule.init();
        ExportModule.init();
        MasteryModule.init();
        AdminPanel.init();

        // Bind history filters
        ["history-filter-subject", "history-filter-status"].forEach(function (id) {
            var el = $(id);
            if (el) el.addEventListener("change", function () { HistoryModule._resetPage(); HistoryModule.load(); });
        });
        var searchEl = $("history-search");
        if (searchEl) {
            var _searchTimer = null;
            searchEl.addEventListener("input", function () {
                clearTimeout(_searchTimer);
                _searchTimer = setTimeout(function () { HistoryModule._resetPage(); HistoryModule.load(); }, 400);
            });
        }

        var enterBatchBtn = $("btn-enter-batch");
        if (enterBatchBtn) enterBatchBtn.addEventListener("click", function () { HistoryModule.enterBatchMode(); });
        var exitBatchBtn = $("btn-exit-batch");
        if (exitBatchBtn) exitBatchBtn.addEventListener("click", function () { HistoryModule.exitBatchMode(); });
        var selectAllCb = $("history-select-all");
        if (selectAllCb) selectAllCb.addEventListener("change", function () { HistoryModule.toggleSelectAll(); });
        var batchDeleteBtn = $("btn-batch-delete");
        if (batchDeleteBtn) batchDeleteBtn.addEventListener("click", function () { HistoryModule.batchDelete(); });
        var cleanupSelect = $("history-cleanup-select");
        if (cleanupSelect) cleanupSelect.addEventListener("change", function () {
            var val = this.value;
            if (val !== "") {
                HistoryModule.cleanupByDate(parseInt(val));
                this.value = "";
            }
        });
        var confirmCancelBtn = $("confirm-cancel");
        if (confirmCancelBtn) confirmCancelBtn.addEventListener("click", function () { HistoryModule.hideConfirm(); });

        var perPageSelect = $("pagination-per-page");
        if (perPageSelect) perPageSelect.addEventListener("change", function () {
            HistoryModule._perPage = parseInt(this.value);
            HistoryModule._resetPage();
            HistoryModule.load();
        });
        var btnFirst = $("btn-page-first");
        if (btnFirst) btnFirst.addEventListener("click", function () { if (HistoryModule._page > 1) { HistoryModule._page = 1; HistoryModule.load(); } });
        var btnPrev = $("btn-page-prev");
        if (btnPrev) btnPrev.addEventListener("click", function () { if (HistoryModule._page > 1) { HistoryModule._page--; HistoryModule.load(); } });
        var btnNext = $("btn-page-next");
        if (btnNext) btnNext.addEventListener("click", function () { HistoryModule._page++; HistoryModule.load(); });
        var btnLast = $("btn-page-last");
        if (btnLast) btnLast.addEventListener("click", function () { var tp = Math.ceil(HistoryModule._total / HistoryModule._perPage) || 1; HistoryModule._page = tp; HistoryModule.load(); });



        var form = $("analysis-form");
        var runBtn = $("run-btn");
        var btnText = $("btn-text");
        var btnLoading = $("btn-loading");
        var loadingDiv = $("loading");
        var errorMsg = $("error-msg");
        var resultsDiv = $("results");
        var textMode = $("text-mode");
        var ocrMode = $("ocr-mode");
        var imageUpload = $("image-upload");
        var imageUploadCamera = $("image-upload-camera");
        var imagePreview = $("image-preview");
        var previewImg = $("preview-img");
        var removeImageBtn = $("remove-image");
        var uploadArea = $("upload-area");

        var problemTextMode = $("problem-text-mode");
        var problemOcrMode = $("problem-ocr-mode");
        var problemImageUpload = $("problem-image-upload");
        var problemImageUploadCamera = $("problem-image-upload-camera");
        var problemImagePreview = $("problem-image-preview");
        var problemPreviewImg = $("problem-preview-img");
        var removeProblemImageBtn = $("remove-problem-image");
        var problemUploadArea = $("problem-upload-area");

        var problemOcrResult = $("problem-ocr-result");
        var problemOcrText = $("problem-ocr-text");
        var problemOcrQuality = $("problem-ocr-quality");
        var problemOcrLoading = $("problem-ocr-loading");
        var problemOcrPreview = $("problem-ocr-preview");

        var subjectSelect = $("subject-select");

        var solutionOcrResult = $("solution-ocr-result");
        var solutionOcrText = $("solution-ocr-text");
        var solutionOcrQuality = $("solution-ocr-quality");
        var solutionOcrLoading = $("solution-ocr-loading");
        var solutionOcrPreview = $("solution-ocr-preview");


        function resetForm() {
            $("problem-text").value = "";
            $("student-solution").value = "";
            if (problemOcrText) problemOcrText.value = "";
            if (solutionOcrText) solutionOcrText.value = "";
            var psText = document.querySelector('input[name="problem_source"][value="text"]');
            if (psText) psText.checked = true;
            var stText = document.querySelector('input[name="source_type"][value="text"]');
            if (stText) stText.checked = true;
            var depthStd = document.querySelector('input[name="depth"][value="standard"]');
            if (depthStd) depthStd.checked = true;
            if (subjectSelect) subjectSelect.value = "auto_detect";
            var modelSel = $("model-select");
            if (modelSel) modelSel.value = "qwen/qwen3.6-plus";
            var modeSel = $("mode-select");
            if (modeSel) modeSel.value = "workflow_r1";
            if (removeImageBtn) removeImageBtn.click();
            if (removeProblemImageBtn) removeProblemImageBtn.click();
            if (textMode) textMode.click();
            if (problemTextMode) problemTextMode.click();
            var outputArea = $("output-area");
            if (outputArea) outputArea.style.display = "none";
            errorMsg.style.display = "none";
        }

        $("crop-cancel").addEventListener("click", closeCropModal);
        $("crop-confirm").addEventListener("click", confirmCrop);

        var chatInput = $("chat-input");
        var chatSendBtn = $("chat-send-btn");
        if (chatSendBtn) chatSendBtn.addEventListener("click", sendChatMessage);
        if (chatInput) {
            chatInput.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
            });
        }

        problemOcrText.addEventListener("input", function () {
            debounceRenderOcrPreview("problem", problemOcrText.value, problemOcrPreview);
        });
        solutionOcrText.addEventListener("input", function () {
            debounceRenderOcrPreview("solution", solutionOcrText.value, solutionOcrPreview);
        });

        document.querySelectorAll('input[name="problem_source"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                if (this.value === "text") { problemTextMode.style.display = ""; problemOcrMode.style.display = "none"; }
                else { problemTextMode.style.display = "none"; problemOcrMode.style.display = ""; }
            });
        });

        subjectSelect.addEventListener("change", function () {
            updateSubjectLabels(this.value);
        });
        updateSubjectLabels(subjectSelect.value);

        var modeSelect = $("mode-select");
        var modelSelect = $("model-select");
        var modelGroup = $("model-group");

        function updateModelVisibility() {
            if (modelGroup) modelGroup.style.display = modeSelect.value === "workflow_r1" ? "" : "none";
        }
        modeSelect.addEventListener("change", updateModelVisibility);
        updateModelVisibility();

        var DEPTH_HINTS = {
            no_ref: "\u8df3\u8fc7\u53c2\u8003\u89e3\u7b54\uff0c\u76f4\u63a5\u9a8c\u8bc1\u6b65\u9aa4\u2014\u2014\u66f4\u5feb\uff0c\u9002\u5408\u7b80\u5355\u9898\u76ee",
            with_ref: "\u5148\u751f\u6210\u53c2\u8003\u89e3\u7b54\u518d\u9010\u6b65\u9a8c\u8bc1\u2014\u2014\u66f4\u51c6\uff0c\u9002\u5408\u590d\u6742\u9898\u76ee"
        };
        document.querySelectorAll('input[name="depth"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                var hintEl = $("depth-hint-text");
                if (hintEl) hintEl.textContent = DEPTH_HINTS[radio.value] || "";
                document.querySelectorAll(".depth-option").forEach(function (el) { el.classList.remove("depth-option-active"); });
                radio.closest(".depth-option").classList.add("depth-option-active");
            });
        });

        var _subjectDetectTimer = null;
        var _userManuallyChanged = false;
        var _subjectStatusEl = $("subject-detect-status");

        subjectSelect.addEventListener("change", function () {
            _userManuallyChanged = (this.value !== "auto_detect");
            _subjectStatusEl.style.display = "none";
        });

        function setSubjectStatus(status, text) {
            _subjectStatusEl.style.display = "";
            _subjectStatusEl.className = "subject-status " + status;
            _subjectStatusEl.innerHTML = text;
        }

        function hideSubjectStatus() {
            _subjectStatusEl.style.display = "none";
            _subjectStatusEl.className = "subject-status";
            _subjectStatusEl.innerHTML = "";
        }

        function autoDetectSubject(text) {
            if (_userManuallyChanged) return;
            if (!text || text.trim().length < 3) { hideSubjectStatus(); return; }
            if (_subjectDetectTimer) clearTimeout(_subjectDetectTimer);
            setSubjectStatus("detecting", '<span class="spinner-small"></span> \u8bc6\u522b\u5b66\u79d1\u4e2d...');
            _subjectDetectTimer = setTimeout(function () {
                fetch("/detect-subject", {
                    method: "POST",
                    headers: Object.assign({ "Content-Type": "application/x-www-form-urlencoded" }, AuthModule.getAuthHeader()),
                    body: "problem_text=" + encodeURIComponent(text),
                })
                    .then(function (resp) { return resp.json(); })
                    .then(function (data) {
                        if (data.subject_id) {
                            var name = data.display_name || data.subject_id;
                            setSubjectStatus("detected", "\u2713 \u5df2\u8bc6\u522b\u4e3a\uff1a" + name);
                        }
                    })
                    .catch(function () { hideSubjectStatus(); });
            }, 2000);
        }

        $("problem-text").addEventListener("input", function () { autoDetectSubject(this.value); });

        function handleProblemImage(file) {
            if (!file) return;
            var model = $("model-select").value;
            openCropModal(file, function (croppedFile) {
                var reader = new FileReader();
                reader.onload = function (e) {
                    problemPreviewImg.src = e.target.result;
                    problemImagePreview.style.display = "";
                    problemUploadArea.style.display = "none";
                };
                reader.readAsDataURL(croppedFile);
                showOcrLoading(problemOcrResult, problemOcrText, problemOcrQuality, problemOcrLoading);
                callOcr(croppedFile, model,
                    function (r) {
                        showOcrSuccess(problemOcrResult, problemOcrText, problemOcrQuality, problemOcrLoading, r);
                        renderOcrPreview(problemOcrText.value, problemOcrPreview);
                        autoDetectSubject(problemOcrText.value);
                    },
                    function () { showOcrError(problemOcrResult, problemOcrText, problemOcrQuality, problemOcrLoading); }
                );
            });
        }

        problemImageUpload.addEventListener("change", function () { if (this.files[0]) handleProblemImage(this.files[0]); });
        problemImageUploadCamera.addEventListener("change", function () { if (this.files[0]) handleProblemImage(this.files[0]); });

        removeProblemImageBtn.addEventListener("click", function () {
            problemImageUpload.value = "";
            problemImageUploadCamera.value = "";
            problemImagePreview.style.display = "none";
            problemPreviewImg.src = "";
            problemUploadArea.style.display = "";
            hideOcr(problemOcrResult, problemOcrText, problemOcrQuality, problemOcrLoading);
            problemOcrPreview.innerHTML = "";
            problemOcrPreview.classList.add("is-empty");
        });

        setupDragDrop(problemUploadArea, problemImageUpload);

        document.querySelectorAll('input[name="source_type"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                if (this.value === "text") { textMode.style.display = ""; ocrMode.style.display = "none"; }
                else { textMode.style.display = "none"; ocrMode.style.display = ""; }
            });
        });

        function handleSolutionImage(file) {
            if (!file) return;
            var model = $("model-select").value;
            openCropModal(file, function (croppedFile) {
                var reader = new FileReader();
                reader.onload = function (e) {
                    previewImg.src = e.target.result;
                    imagePreview.style.display = "";
                    uploadArea.style.display = "none";
                };
                reader.readAsDataURL(croppedFile);
                showOcrLoading(solutionOcrResult, solutionOcrText, solutionOcrQuality, solutionOcrLoading);
                callOcr(croppedFile, model,
                    function (r) {
                        showOcrSuccess(solutionOcrResult, solutionOcrText, solutionOcrQuality, solutionOcrLoading, r);
                        renderOcrPreview(solutionOcrText.value, solutionOcrPreview);
                    },
                    function () { showOcrError(solutionOcrResult, solutionOcrText, solutionOcrQuality, solutionOcrLoading); }
                );
            });
        }

        imageUpload.addEventListener("change", function () { if (this.files[0]) handleSolutionImage(this.files[0]); });
        imageUploadCamera.addEventListener("change", function () { if (this.files[0]) handleSolutionImage(this.files[0]); });

        removeImageBtn.addEventListener("click", function () {
            imageUpload.value = "";
            imageUploadCamera.value = "";
            imagePreview.style.display = "none";
            previewImg.src = "";
            uploadArea.style.display = "";
            hideOcr(solutionOcrResult, solutionOcrText, solutionOcrQuality, solutionOcrLoading);
            solutionOcrPreview.innerHTML = "";
            solutionOcrPreview.classList.add("is-empty");
        });

        setupDragDrop(uploadArea, imageUpload);

        function setupDragDrop(area, input) {
            area.addEventListener("dragover", function (e) { e.preventDefault(); area.style.borderColor = "var(--accent)"; });
            area.addEventListener("dragleave", function () { area.style.borderColor = ""; });
            area.addEventListener("drop", function (e) {
                e.preventDefault();
                area.style.borderColor = "";
                if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event("change")); }
            });
        }

        var cancelAnalysisBtn = $("cancel-analysis-btn");
        if (cancelAnalysisBtn) cancelAnalysisBtn.addEventListener("click", cancelAnalysis);

        var newDiagBtn = $("btn-new-diagnosis");
        if (newDiagBtn) newDiagBtn.addEventListener("click", function () { resetForm(); InputPanel.expand(); $("input-panel").scrollIntoView({ behavior: "smooth", block: "start" }); });

        form.addEventListener("submit", function (e) {
            e.preventDefault();

            var problemSource = document.querySelector('input[name="problem_source"]:checked').value;
            var sourceType = document.querySelector('input[name="source_type"]:checked').value;
            var problemText, studentSolution;

            if (problemSource === "ocr") problemText = problemOcrText.value.trim();
            else problemText = $("problem-text").value.trim();

            if (sourceType === "ocr") studentSolution = solutionOcrText.value.trim();
            else studentSolution = $("student-solution").value.trim();

            if (!problemText) { showError("\u8bf7\u8f93\u5165\u9898\u76ee"); return; }
            if (sourceType === "text" && !studentSolution) { showError("\u8bf7\u8f93\u5165\u5b66\u751f\u7684\u89e3\u9898\u6b65\u9aa4"); return; }
            if (sourceType === "ocr" && !studentSolution) { showError("\u8bf7\u5148\u4e0a\u4f20\u89e3\u9898\u7167\u7247\u5e76\u7b49\u5f85 OCR \u8bc6\u522b\u5b8c\u6210"); return; }

            var formData = new FormData();
            formData.append("problem_text", problemText);
            formData.append("source_type", "text");
            formData.append("student_solution", studentSolution);
            formData.append("model", $("model-select").value);
            formData.append("subject_id", $("subject-select").value);
            formData.append("mode", $("mode-select").value);
            var depthRadio = document.querySelector('input[name="depth"]:checked');
            if (depthRadio) formData.append("depth", depthRadio.value);

            InputPanel.collapse();
            var lt = $("loading").querySelector(".loading-title");
            if (lt) lt.textContent = "正在分析诊断...";
            showLoading();
            if (depthRadio && depthRadio.value === "no_ref") {
                var refStep = loadingDiv.querySelector('.loading-step[data-step="reference"]');
                if (refStep) refStep.style.display = "none";
            }
            startStreamWithReconnect(formData);
        });

        var currentRunId = null;
        var currentBatchId = null;
        var reconnectTimer = null;
        var abortController = null;

        function cancelAnalysis() {
            localStorage.removeItem("stem_tutor_pending_run");
            if (currentBatchId) {
                fetch("/batch/" + currentBatchId + "/cancel", { method: "POST", headers: AuthModule.getAuthHeader() }).catch(function () {});
            }
            if (currentRunId) {
                fetch("/analyze/cancel/" + currentRunId, { method: "POST", headers: AuthModule.getAuthHeader() }).catch(function () {});
            }
            if (abortController) { abortController.abort(); abortController = null; }
            if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
            hideLoading();
            InputPanel.expand();
        }

        function startStreamWithReconnect(formData) {
            currentRunId = null;
            currentBatchId = null;
            if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
            abortController = new AbortController();
            fetch("/analyze/stream", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData, signal: abortController.signal })
                .then(function (resp) {
                    if (!resp.ok) {
                        return resp.json().catch(function () { return {}; }).then(function (err) {
                            throw new Error(err.error || "HTTP " + resp.status);
                        });
                    }
                    return readSSEStream(resp.body);
                })
                .then(function (data) {
                    localStorage.removeItem("stem_tutor_pending_run");
                    if (data && data._batchId) {
                        pollBatchUntilComplete(data._batchId);
                    } else if (data) {
                        renderResults(data);
                    }
                })
                .catch(function (err) {
                    if (err.name === "AbortError") return;
                    if (currentRunId) { showReconnectingUI(currentRunId); }
                    else { showError("\u8bf7\u6c42\u5931\u8d25\uff1a" + err.message); }
                })
                .finally(function () { abortController = null; });
        }

        function loadResultAndRender(runId) {
            return fetch("/analyze/result/" + runId, { headers: AuthModule.getAuthHeader() })
                .then(function (r) {
                    if (!r.ok) {
                        return r.json().catch(function () { return {}; }).then(function (d) {
                            throw new Error(d.error || d.message || ("HTTP " + r.status));
                        });
                    }
                    return r.json();
                })
                .then(function (result) {
                    renderResults(result);
                    hideLoading();
                });
        }

        function showReconnectingUI(runId) {
            currentRunId = runId;
            localStorage.setItem("stem_tutor_pending_run", runId);
            var pollInterval = 3000;
            var maxPolls = 600;
            var pollCount = 0;
            var startTime = Date.now();
            function formatElapsed() {
                var sec = Math.floor((Date.now() - startTime) / 1000);
                if (sec >= 60) return Math.floor(sec / 60) + "\u5206" + (sec % 60) + "\u79d2";
                return sec + "\u79d2";
            }
            function pollStatus() {
                if (pollCount >= maxPolls) { showError("\u67e5\u8be2\u7ed3\u679c\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5\u3002"); hideLoading(); return; }
                pollCount++;
                fetch("/analyze/status/" + runId, { headers: AuthModule.getAuthHeader() })
                    .then(function (resp) { return resp.json(); })
                    .then(function (data) {
                        var status = normalizeRunStatus(data.user_status || data.status);
                        if (status === "complete" || status === "needs_review" || status === "unavailable" || status === "cancelled") {
                            loadResultAndRender(runId).catch(function (err) {
                                showError((err && err.message) || data.message || "\u83b7\u53d6\u7ed3\u679c\u5931\u8d25\u3002");
                                hideLoading();
                            });
                        } else {
                            if (data.completed_nodes) {
                                data.completed_nodes.forEach(function(n) { completeStep(n); });
                            }
                            if (data.last_node) {
                                renderPartial(data.last_node, data);
                            }
                            if (data.steps && data.steps.length > 0) {
                                var stepsContainer = $("steps-table-container");
                                if (stepsContainer && !stepsContainer.innerHTML.trim()) {
                                    renderStepsPartial(data.steps);
                                }
                            }
                            var statusEl = loadingDiv.querySelector(".loading-status");
                            if (statusEl) {
                                statusEl.style.display = "";
                                var label = data.last_node_label || "";
                                var msg = label
                                    ? label + " \u5df2\u5b8c\u6210\uff08\u5df2\u7b49\u5f85 " + formatElapsed() + "\uff09"
                                    : "\u540e\u7aef\u4ecd\u5728\u8fd0\u884c...\uff08\u5df2\u7b49\u5f85 " + formatElapsed() + "\uff09";
                                statusEl.textContent = msg;
                            }
                            reconnectTimer = setTimeout(pollStatus, pollInterval);
                        }
                    })
                    .catch(function () { reconnectTimer = setTimeout(pollStatus, pollInterval); });
            }
            pollStatus();
        }

        function pollBatchUntilComplete(batchId) {
            var pollInterval = 3000;
            var maxPolls = 600;
            var pollCount = 0;
            var startTime = Date.now();
            function formatElapsed() {
                var sec = Math.floor((Date.now() - startTime) / 1000);
                if (sec >= 60) return Math.floor(sec / 60) + "\u5206" + (sec % 60) + "\u79d2";
                return sec + "\u79d2";
            }
            function pollBatch() {
                if (pollCount >= maxPolls) {
                    showError("\u67e5\u8be2\u7ed3\u679c\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5\u3002");
                    hideLoading();
                    return;
                }
                pollCount++;
                fetch("/analyze/batch-status/" + batchId, { headers: AuthModule.getAuthHeader() })
                    .then(function (resp) {
                        if (!resp.ok) throw new Error("HTTP " + resp.status);
                        return resp.json();
                    })
                    .then(function (data) {
                        if (data.run_id) {
                            currentRunId = data.run_id;
                            showReconnectingUI(data.run_id);
                            return;
                        }
                        if (data.status === "failed") {
                            showError(data.error || "\u5206\u6790\u5931\u8d25");
                            hideLoading();
                            return;
                        }
                        var statusEl = loadingDiv.querySelector(".loading-status");
                        if (statusEl) {
                            statusEl.style.display = "";
                            statusEl.textContent = "\u6b63\u5728\u5206\u6790\u8bca\u65ad...\uff08\u5df2\u7b49\u5f85 " + formatElapsed() + "\uff09";
                        }
                        reconnectTimer = setTimeout(pollBatch, pollInterval);
                    })
                    .catch(function () {
                        reconnectTimer = setTimeout(pollBatch, pollInterval);
                    });
            }
            pollBatch();
        }

        function readSSEStream(body) {
            var reader = body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buffer = "";
            var resultData = null;
            var batchId = null;

            function readNext() {
                return reader.read().then(function (chunk) {
                    if (chunk.done) {
                        if (batchId) {
                            return { _batchId: batchId };
                        }
                        if (resultData) return resultData;
                        throw new Error("\u6d41\u7ed3\u675f\u4f46\u672a\u6536\u5230\u5b8c\u6574\u7ed3\u679c");
                    }
                    buffer += decoder.decode(chunk.value, { stream: true });
                    var lines = buffer.split("\n");
                    buffer = lines.pop() || "";
                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i].trim();
                        if (!line || !line.startsWith("data:")) continue;
                        var jsonStr = line.substring(5).trim();
                        try { var event = JSON.parse(jsonStr); } catch (e) { continue; }
                        if (event.type === "start") {
                            currentRunId = event.run_id || null;
                            if (event.run_id) localStorage.setItem("stem_tutor_pending_run", event.run_id);
                            if (event.batch_id) { batchId = event.batch_id; currentBatchId = event.batch_id; }
                            var cancelBtn = $("cancel-analysis-btn");
                            if (cancelBtn) cancelBtn.style.display = "";
                        } else if (event.type === "retrying") {
                            var statusEl2 = loadingDiv.querySelector(".loading-status");
                            if (statusEl2) {
                                statusEl2.style.display = "";
                                statusEl2.textContent = event.message || "\u6b63\u5728\u81ea\u52a8\u91cd\u8bd5...";
                            }
                        } else if (event.type === "node_start") {
                            activateStep(event.node);
                        } else if (event.type === "progress") {
                            updateStepDetail(event.node, event.detail);
                        } else if (event.type === "node_done") {
                            completeStep(event.node);
                            if (event.partial) renderPartial(event.node, event.partial);
                        } else if (event.type === "safe_error") {
                            var statusEl3 = loadingDiv.querySelector(".loading-status");
                            if (statusEl3) {
                                statusEl3.style.display = "";
                                statusEl3.textContent = event.message || "\u5206\u6790\u7ed3\u679c\u6682\u4e0d\u53ef\u7528\u3002";
                            }
                        } else if (event.type === "cancelled") {
                            localStorage.removeItem("stem_tutor_pending_run");
                            currentRunId = null;
                            hideLoading();
                            InputPanel.expand();
                            return;
                        } else if (event.type === "result") {
                            resultData = event.data;
                        } else if (event.type === "error") {
                            throw new Error(event.message || "\u672a\u77e5\u9519\u8bef");
                        }
                    }
                    return readNext();
                });
            }
            return readNext();
        }

        var nodeToStepMap = {
            "parse_student_solution": "parse",
            "generate_reference_solution": "reference",
            "verify_steps": "verify",
            "diagnose_error": "diagnose",
            "generate_feedback": "feedback",
            "generate_review_problems": "review",
        };

        function activateStep(nodeName) {
            var stepKey = nodeToStepMap[nodeName];
            if (!stepKey) return;
            loadingDiv.querySelectorAll(".loading-step").forEach(function (el) {
                if (el.getAttribute("data-step") === stepKey) {
                    el.classList.add("active");
                    el.classList.remove("done");
                    var icon = el.querySelector(".loading-step-icon");
                    if (icon) icon.innerHTML = "\u2610";
                }
            });
        }

        function updateStepDetail(nodeName, detail) {
            var stepKey = nodeToStepMap[nodeName];
            if (!stepKey) return;
            loadingDiv.querySelectorAll(".loading-step").forEach(function (el) {
                if (el.getAttribute("data-step") === stepKey) {
                    var detailEl = el.querySelector(".loading-step-detail");
                    if (detailEl) { detailEl.textContent = detail; detailEl.style.display = ""; }
                }
            });
        }

        function completeStep(nodeName) {
            var stepKey = nodeToStepMap[nodeName];
            if (!stepKey) return;
            loadingDiv.querySelectorAll(".loading-step").forEach(function (el) {
                if (el.getAttribute("data-step") === stepKey) {
                    el.classList.remove("active");
                    el.classList.add("done");
                    var icon = el.querySelector(".loading-step-icon");
                    if (icon) icon.innerHTML = "\u2611";
                }
            });
        }

        function resetLoadingSteps() {
            loadingDiv.querySelectorAll(".loading-step").forEach(function (el) {
                el.classList.remove("active", "done");
                el.style.display = "";
                var icon = el.querySelector(".loading-step-icon");
                if (icon) icon.innerHTML = "\u2610";
                var detailEl = el.querySelector(".loading-step-detail");
                if (detailEl) { detailEl.textContent = ""; detailEl.style.display = "none"; }
            });
        }

        function showLoading() {
            $("output-area").style.display = "";
            runBtn.disabled = true;
            btnText.style.display = "none";
            btnLoading.style.display = "";
            loadingDiv.style.display = "";
            resultsDiv.style.display = "none";
            var exportArea = $("export-area");
            if (exportArea) exportArea.style.display = "none";
            errorMsg.style.display = "none";
            resetLoadingSteps();
            resetPartialRenders();
        }

        function resetPartialRenders() {
            $("steps-table-container").innerHTML = "";
            $("diagnosis-container").innerHTML = "";
            $("feedback-container").innerHTML = "";
            $("review-container").innerHTML = "";
            $("summary-card").innerHTML = "";
            $("summary-card").style.display = "none";
            $("run-info").innerHTML = "";
            $("raw-json").textContent = "";
            $("diagnosis-section").style.display = "none";
            $("feedback-section").style.display = "none";
            $("review-section").style.display = "none";
            $("reference-section").style.display = "none";
            $("reference-container").innerHTML = "";
            $("chat-section").style.display = "none";
            $("chat-messages").innerHTML = "";
            _chatRunId = null;
        }

        function hideLoading() {
            runBtn.disabled = false;
            btnText.style.display = "";
            btnLoading.style.display = "none";
            loadingDiv.style.display = "none";
            var cancelBtn = $("cancel-analysis-btn");
            if (cancelBtn) cancelBtn.style.display = "none";
        }

        function showError(msg) {
            errorMsg.textContent = msg;
            errorMsg.style.display = "";
            resultsDiv.style.display = "none";
        }

        function renderPartial(nodeName, data) {
            resultsDiv.style.display = "";
            if (nodeName === "parse_student_solution") renderStepsPartial(data.steps);
            else if (nodeName === "generate_reference_solution") renderReference(data);
            else if (nodeName === "verify_steps") renderSteps(data.steps, (currentRunId || null));
            else if (nodeName === "diagnose_error") renderDiagnoses(data.diagnoses);
            else if (nodeName === "generate_feedback") renderFeedback(data);
            else if (nodeName === "generate_review_problems") renderReview(data.review_problems);
            renderAllMath(resultsDiv);
        }

        function renderStepsPartial(steps) {
            var el = $("steps-table-container");
            if (!steps || !steps.length) { el.innerHTML = '<p class="summary-text">\u65e0\u6b65\u9aa4\u6570\u636e\u3002</p>'; return; }
            var html = '<table class="steps-table"><thead><tr><th>\u6b65\u9aa4</th><th>\u5185\u5bb9</th><th>\u7ed3\u679c</th></tr></thead><tbody>';
            steps.forEach(function (s) {
                var stepCellId = "step-cell-" + s.step_id.replace(/\s+/g, "-");
                html += "<tr><td>" + esc(s.step_id) + "</td>" +
                    '<td class="step-text"><div id="' + stepCellId + '" class="step-text-inner"></div></td>' +
                    '<td><span class="label-badge label-unclear">\u7b49\u5f85\u9a8c\u8bc1</span></td></tr>';
            });
            html += "</tbody></table>";
            el.innerHTML = html;
            $("steps-section").style.display = "";
            steps.forEach(function (s) {
                var stepCellId = "step-cell-" + s.step_id.replace(/\s+/g, "-");
                var container = document.getElementById(stepCellId);
                if (container) renderStepContent(s.raw_text, container);
            });
        }

        function updateSubjectLabels(subjectId) {
            document.title = SITE_TITLE;
        }
    }

    function init() {
        AuthModule.init();
        var pendingRunId = localStorage.getItem("stem_tutor_pending_run");
        if (!pendingRunId) return;
        fetch("/analyze/status/" + pendingRunId, { headers: AuthModule.getAuthHeader() })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var st = normalizeRunStatus(data.user_status || data.status);
                if (st === "running" || st === "pending") {
                    showLoading();
                    InputPanel.collapse();
                    showReconnectingUI(pendingRunId);
                } else {
                    localStorage.removeItem("stem_tutor_pending_run");
                }
            })
            .catch(function () {
                localStorage.removeItem("stem_tutor_pending_run");
            });
    }

    /* ===== Result Rendering (global scope for HistoryModule) ===== */
    function renderStepContent(text, container) {
        if (!window.katex || !text || !text.trim()) { container.textContent = text || ""; return; }
        var processed = preprocessLatex(text);
        var lines = processed.split("\n");
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i].trim();
            if (!line && lines.length > 1) continue;
            var div = document.createElement("div");
            div.className = "step-math-line";
            try { katex.render(line, div, { throwOnError: true, displayMode: false, strict: false, trust: true }); }
            catch (e) {
                try { katex.render(line, div, { throwOnError: false, displayMode: false, strict: false, trust: true }); }
                catch (e2) { div.className = "step-math-line step-math-error"; div.textContent = line; }
            }
            container.appendChild(div);
        }
    }

    function renderResults(data) {
        _lastResult = data;
        var resultsDiv = $("results");
        $("ocr-warning").style.display = "none";
        $("status-banner").style.display = "none";
        if (data.ocr_meta) renderOcrWarning(data.ocr_meta, data.ocr_extracted_text);

        var userStatus = normalizeRunStatus(data.user_status || data.status);
        if (userStatus === "unavailable") {
            var unavailableBanner = $("status-banner");
            unavailableBanner.className = "alert alert-error";
            unavailableBanner.textContent = data.user_message || "\u5206\u6790\u7ed3\u679c\u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002";
            unavailableBanner.style.display = "";

            renderSummary(data);
            $("steps-table-container").innerHTML = '<p class="summary-text">\u5f53\u524d\u7ed3\u679c\u53d7\u4fdd\u62a4\uff0c\u6682\u4e0d\u5c55\u793a\u8be6\u7ec6\u6b65\u9aa4\u3002</p>';
            $("diagnosis-section").style.display = "none";
            $("feedback-section").style.display = "none";
            $("review-section").style.display = "none";
            renderAdvanced(data);
            resultsDiv.style.display = "";
            var exportArea = $("export-area");
            if (exportArea) exportArea.style.display = "";
            return;
        }

        if (userStatus === "needs_review") {
            var reviewBanner = $("status-banner");
            reviewBanner.className = "alert alert-warning";
            reviewBanner.textContent = data.user_message || "\u7ed3\u679c\u7f6e\u4fe1\u5ea6\u4e0d\u8db3\uff0c\u5efa\u8bae\u4eba\u5de5\u590d\u6838\u3002";
            reviewBanner.style.display = "";
        }

        renderSummary(data);
        renderReference(data);
        renderSteps(data.steps, (data.run_meta || {}).run_id);
        renderDiagnoses(data.diagnoses);
        renderFeedback(data);
        renderReview(data.review_problems);
        renderAdvanced(data);

        resultsDiv.style.display = "";
        var exportArea = $("export-area");
        if (exportArea) exportArea.style.display = "";
        renderAllMath(resultsDiv);
        var meta = data.run_meta || {};
        if (meta.run_id) initChat(meta.run_id);
    }

    function renderOcrWarning(meta, extractedText) {
        var score = meta.quality_score || 0;
        if (score >= 0.7) return;
        var el = $("ocr-warning");
        var isLow = score < 0.4;
        el.className = "alert " + (isLow ? "alert-error" : "alert-warning");
        var severity = isLow ? "\u6781\u4f4e" : "\u8f83\u4f4e";
        var advice = isLow ? "\u5efa\u8bae\u91cd\u65b0\u62cd\u7167\u6216\u624b\u52a8\u8f93\u5165\u3002" : "\u90e8\u5206\u5185\u5bb9\u53ef\u80fd\u8bc6\u522b\u4e0d\u51c6\u786e\uff0c\u8bf7\u4ed4\u7ec6\u6838\u5bf9\u3002";
        var html = "<strong>OCR \u8bc6\u522b\u8d28\u91cf" + severity + "\uff08\u5f97\u5206 " + score.toFixed(2) + "\uff09</strong>\uff0c" + advice;
        if (meta.warnings && meta.warnings.length) html += "<br>\u8b66\u544a\uff1a" + esc(meta.warnings.join("\uff1b"));
        if (extractedText) html += '<div class="ocr-text-box">' + esc(extractedText) + "</div>";
        el.innerHTML = html;
        el.style.display = "";
    }

    function renderSummary(data) {
        var el = $("summary-card");
        el.style.display = "";
        var hasError = data.first_critical_step_id;
        var userStatus = normalizeRunStatus(data.user_status || data.status);
        if (userStatus === "unavailable") {
            el.innerHTML = '<p class="summary-text">\u7ed3\u679c\u6682\u4e0d\u53ef\u7528\uff0c\u7cfb\u7edf\u5df2\u81ea\u52a8\u4fdd\u62a4\u8f93\u51fa\u3002\u8bf7\u7a0d\u540e\u91cd\u8bd5\u6216\u91cd\u65b0\u5206\u6790\u3002</p>';
            return;
        }
        if (!hasError) {
            el.innerHTML = '<p class="summary-highlight" style="color:var(--success-text);">\u6240\u6709\u6b65\u9aa4\u901a\u8fc7\u9a8c\u8bc1</p>' +
                (data.concise_summary ? '<p class="summary-text">' + esc(data.concise_summary) + "</p>" : "");
            return;
        }
        var html = '<p class="summary-highlight" style="color:var(--error-text);">\u9996\u4e2a\u5173\u952e\u9519\u8bef\uff1a\u6b65\u9aa4 ' + esc(data.first_critical_step_id) + "</p>" +
            '<p class="summary-text"><span class="summary-label">\u6458\u8981\uff1a</span>' + esc(data.concise_summary) + "</p>";
        if (data.likely_cause) html += '<p class="summary-text"><span class="summary-label">\u53ef\u80fd\u539f\u56e0\uff1a</span>' + esc(data.likely_cause) + "</p>";
        if (data.next_action) html += '<p class="summary-text"><span class="summary-label">\u5efa\u8bae\u884c\u52a8\uff1a</span>' + esc(data.next_action) + "</p>";
        if (data.caution_note) html += '<p class="summary-text" style="color:var(--warning-text);"><span class="summary-label">\u6ce8\u610f\u4e8b\u9879\uff1a</span>' + esc(data.caution_note) + "</p>";
        el.innerHTML = html;
    }

    function renderReference(data) {
        var el = $("reference-section");
        var container = $("reference-container");
        var ref = data.reference_solution;
        if (!ref || (!ref.reference_text && !ref.key_assertions)) {
            el.style.display = "none";
            return;
        }
        el.style.display = "";
        var html = "";
        if (ref.reference_text) {
            var processed = preprocessLatex(ref.reference_text);
            var rendered = window.marked ? marked.parse(processed) : esc(processed);
            html += '<div class="reference-text">参考解答：</div>';
            html += '<div class="reference-content">' + rendered + '</div>';
        }
        if (ref.key_assertions && ref.key_assertions.length) {
            html += '<div class="reference-assertions"><strong>关键断言：</strong><ul>';
            ref.key_assertions.forEach(function (a) {
                html += '<li>' + esc(a) + '</li>';
            });
            html += '</ul></div>';
        }
        container.innerHTML = html;
    }

    function renderSteps(steps, runId) {
        var el = $("steps-table-container");
        if (!steps || !steps.length) { el.innerHTML = '<p class="summary-text">\u65e0\u6b65\u9aa4\u6570\u636e\u3002</p>'; return; }
        var html = '<table class="steps-table"><thead><tr><th>\u6b65\u9aa4</th><th>\u5185\u5bb9</th><th>\u7ed3\u679c</th><th>\u7f6e\u4fe1\u5ea6</th><th>\u64cd\u4f5c</th></tr></thead><tbody>';
        steps.forEach(function (s) {
            var info = LABEL_MAP[s.label] || LABEL_MAP.unclear;
            var confPct = (s.confidence * 100).toFixed(0) + "%";
            var stepCellId = "step-cell-" + s.step_id.replace(/\s+/g, "-");
            html += "<tr><td>" + esc(s.step_id) + "</td>" +
                '<td class="step-text"><div id="' + stepCellId + '" class="step-text-inner"></div></td>' +
                '<td><span class="label-badge ' + info.cls + '">' + info.text + "</span></td>" +
                "<td>" + confPct + "</td>" +
                '<td class="action-cell">';

            var needsReverify = s.confidence < 0.5 ||
                s.label === "unclear" || s.label === "unverified" ||
                (s.violated_principles || []).some(function (p) {
                    return p === "verification_budget_limited" ||
                           p === "verification_default_label" ||
                           p === "verification_batch_mode";
                });
            if (needsReverify) {
                html += '<span class="reverify-hint">\u5efa\u8bae\u9a8c\u8bc1</span> ';
            }
            html += '<button class="btn-reverify" data-run-id="' + esc(runId || "") +
                    '" data-step-id="' + esc(s.step_id) + '">\u91cd\u65b0\u9a8c\u8bc1</button>';
            html += "</td></tr>";

            if (s.label !== "correct" && s.evidence) {
                html += '<tr class="evidence-row"><td></td><td colspan="4">\u8bc1\u636e\uff1a' + esc(s.evidence) + "</td></tr>";
            }
        });
        html += "</tbody></table>";
        el.innerHTML = html;
        steps.forEach(function (s) {
            var stepCellId = "step-cell-" + s.step_id.replace(/\s+/g, "-");
            var container = document.getElementById(stepCellId);
            if (container) renderStepContent(s.raw_text, container);
        });

        document.querySelectorAll(".btn-reverify").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var rid = this.getAttribute("data-run-id");
                var sid = this.getAttribute("data-step-id");
                if (!rid || !sid) return;
                startReverifyStep(rid, sid, this);
            });
        });
    }

    function startReverifyStep(runId, stepId, btn) {
        btn.disabled = true;
        btn.textContent = "\u9a8c\u8bc1\u4e2d...";

        var formData = new FormData();
        formData.append("run_id", runId);
        formData.append("step_id", stepId);

        fetch("/api/verify-step", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    fetch("/analyze/result/" + runId, { headers: AuthModule.getAuthHeader() })
                        .then(function (r) { return r.json(); })
                        .then(function (fullData) {
                            renderResults(fullData);
                            renderAllMath($("results"));
                        });
                } else {
                    btn.disabled = false;
                    btn.textContent = "\u91cd\u65b0\u9a8c\u8bc1";
                    showError(data.error || "\u91cd\u9a8c\u8bc1\u5931\u8d25");
                }
            })
            .catch(function (err) {
                btn.disabled = false;
                btn.textContent = "\u91cd\u65b0\u9a8c\u8bc1";
                showError("\u8bf7\u6c42\u5931\u8d25: " + (err.message || "\u672a\u77e5\u9519\u8bef"));
            });
    }

    function renderDiagnoses(diagnoses) {
        var section = $("diagnosis-section");
        var container = $("diagnosis-container");
        if (!diagnoses || !diagnoses.length) { section.style.display = "none"; return; }
        var html = "";
        diagnoses.forEach(function (d) {
            html += '<div class="diagnosis-card">';
            html += '<p class="diagnosis-step">步骤 ' + esc(d.step_id) + "</p>";
            html += '<span class="diagnosis-code">' + esc(d.error_code) + "</span>";
            if (d.short_desc) html += " " + esc(d.short_desc);
            html += '<p><span class="field-label">类别：</span>' + esc(d.category) + "</p>";
            html += '<p><span class="field-label">假设根因：</span>' + esc(d.root_cause_hypothesis) + "</p>";
            html += '<p><span class="field-label">支持证据：</span>' + esc(d.supporting_evidence) + "</p>";
            html += '<p><span class="field-label">置信度：</span>' + (d.confidence * 100).toFixed(0) + "%</p>";
            var _md = (typeof MasteryModule !== "undefined") ? MasteryModule.getData() : null;
            var _mastered = _md && _md.errors && _md.errors[d.error_code] && _md.errors[d.error_code].mastered;
            html += '<button type="button" class="btn-mastered-toggle' + (_mastered ? ' mastered' : '') + '" data-error-code="' + esc(d.error_code) + '">' + (_mastered ? '已掌握' : '标记已掌握') + '</button>';
            html += "</div>";
            if (typeof MasteryModule !== "undefined" && MasteryModule.recordEncounter) {
                MasteryModule.recordEncounter(d.error_code);
            }
        });
        container.innerHTML = html;
        section.style.display = "";

        container.querySelectorAll(".btn-mastered-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                if (typeof MasteryModule !== "undefined" && MasteryModule.toggleMastered) {
                    MasteryModule.toggleMastered(this.getAttribute("data-error-code"));
                    this.classList.toggle("mastered");
                    this.textContent = this.classList.contains("mastered") ? "已掌握" : "标记已掌握";
                }
            });
        });
    }

    function renderFeedback(data) {
        var section = $("feedback-section");
        var container = $("feedback-container");
        var concepts = data.review_concepts || [];
        var hasFeedback = data.next_action || data.caution_note || concepts.length;
        if (!hasFeedback) { section.style.display = "none"; return; }
        var html = '<ul class="feedback-list">';
        if (concepts.length) html += "<li><strong>\u9700\u590d\u4e60\u6982\u5ff5\uff1a</strong>" + concepts.map(function (c) { return esc(c); }).join("\u3001") + "</li>";
        if (data.next_action) html += "<li><strong>\u4e0b\u4e00\u6b65\u884c\u52a8\uff1a</strong>" + esc(data.next_action) + "</li>";
        if (data.caution_note) html += "<li><strong>\u6ce8\u610f\u4e8b\u9879\uff1a</strong>" + esc(data.caution_note) + "</li>";
        html += "</ul>";
        container.innerHTML = html;
        section.style.display = "";
    }

    function renderReview(problems) {
        var section = $("review-section");
        var container = $("review-container");
        if (!problems || !problems.length) { section.style.display = "none"; return; }
        var html = "";
        problems.forEach(function (p, i) {
            var diff = DIFFICULTY_MAP[p.difficulty_label] || p.difficulty_label || "";
            html += '<div class="review-card" data-review-index="' + i + '">';
            html += '<p class="review-text">(' + (i + 1) + ") " + esc(p.problem_text) + "</p>";
            html += '<p class="review-meta">' + (diff ? '<span class="difficulty-badge">' + esc(diff) + "</span> " : "") + esc(p.rationale) + "</p>";
            html += '<button type="button" class="btn-practice-toggle" data-index="' + i + '">试一试</button>';
            html += '<button type="button" class="btn-secondary btn-sm btn-show-reference" data-index="' + i + '">查看解答</button>';
            html += '<div class="reference-answer-area" id="reference-answer-' + i + '" style="display:none;">';
            html += '<div class="reference-answer-content" id="reference-answer-content-' + i + '"></div>';
            html += "</div>";
            html += '<div class="practice-area" id="practice-area-' + i + '" style="display:none;">';
            html += '<textarea class="practice-input" rows="3" placeholder="输入你的解答..."></textarea>';
            html += '<div class="practice-actions">';
            html += '<button type="button" class="btn-primary btn-sm btn-practice-submit" data-index="' + i + '" disabled>提交验证</button>';
            html += '<button type="button" class="btn-secondary btn-sm btn-practice-cancel" data-index="' + i + '">取消</button>';
            html += '<span class="practice-spinner" style="display:none;"><span class="spinner-small"></span> 验证中...</span>';
            html += "</div>";
            html += '<div class="practice-result" id="practice-result-' + i + '" style="display:none;"></div>';
            html += "</div>";
            html += "</div>";
        });
        container.innerHTML = html;
        section.style.display = "";

        container.querySelectorAll(".btn-practice-toggle").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = this.getAttribute("data-index");
                var area = $("practice-area-" + idx);
                if (area.style.display === "none") {
                    area.style.display = "";
                    this.classList.add("used");
                    this.disabled = true;
                } else {
                    area.style.display = "none";
                }
            });
        });

        container.querySelectorAll(".practice-input").forEach(function (ta) {
            ta.addEventListener("input", function () {
                var card = this.closest(".review-card");
                var submitBtn = card.querySelector(".btn-practice-submit");
                submitBtn.disabled = !this.value.trim();
            });
        });

        container.querySelectorAll(".btn-practice-cancel").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = this.getAttribute("data-index");
                var area = $("practice-area-" + idx);
                if (area) area.style.display = "none";
                var card = this.closest(".review-card");
                var toggleBtn = card.querySelector(".btn-practice-toggle");
                if (toggleBtn) { toggleBtn.disabled = false; toggleBtn.classList.remove("used"); }
                var ta = card.querySelector(".practice-input");
                if (ta) { ta.disabled = false; ta.value = ""; ta.placeholder = "输入你的解答..."; }
                var resultEl = $("practice-result-" + idx);
                if (resultEl) { resultEl.style.display = "none"; resultEl.innerHTML = ""; }
                var submitBtn = card.querySelector(".btn-practice-submit");
                if (submitBtn) submitBtn.disabled = true;
            });
        });

        container.querySelectorAll(".btn-practice-submit").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = parseInt(this.getAttribute("data-index"), 10);
                PracticeModule.submit(idx);
            });
        });

        container.querySelectorAll(".btn-show-reference").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var idx = parseInt(this.getAttribute("data-index"), 10);
                var area = $("reference-answer-" + idx);
                var contentEl = $("reference-answer-content-" + idx);
                if (area.style.display !== "none" && contentEl.getAttribute("data-loaded")) {
                    area.style.display = "none";
                    this.textContent = "查看解答";
                    return;
                }
                area.style.display = "";
                this.textContent = "收起解答";
                if (contentEl.getAttribute("data-loaded")) return;
                contentEl.innerHTML = '<p class="reference-answer-spinner"><span class="spinner-small"></span> 正在生成参考解答（使用计算工具验证）...</p>';
                var problem = problems[idx];
                var subjectId = "calculus";
                if (_lastResult && _lastResult.run_meta && _lastResult.run_meta.subject_id) subjectId = _lastResult.run_meta.subject_id;
                var subjectSelect = $("subject-select");
                if (subjectSelect && subjectSelect.value) subjectId = subjectSelect.value;
                var formData = new FormData();
                formData.append("problem_text", problem.problem_text);
                formData.append("subject_id", subjectId);
                fetch("/practice/reference", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData })
                    .then(function (resp) {
                        if (!resp.ok) return resp.text().then(function (t) { throw new Error(t || "HTTP " + resp.status); });
                        return resp;
                    })
                    .then(function (resp) { return _readReferenceSSE(resp); })
                    .then(function (result) {
                        if (result.reference_text) {
                            contentEl.innerHTML = '<div class="reference-answer-body">' + marked.parse(result.reference_text) + "</div>";
                            if (result.key_assertions && result.key_assertions.length) {
                                var ah = "<p class=\"reference-assertions-title\">关键断言：</p><ul>";
                                result.key_assertions.forEach(function (a) { ah += "<li>" + esc(a) + "</li>"; });
                                ah += "</ul>";
                                contentEl.innerHTML += ah;
                            }
                            renderAllMath(contentEl);
                        } else {
                            contentEl.innerHTML = '<p class="reference-answer-error">生成失败，请重试。</p>';
                        }
                        contentEl.setAttribute("data-loaded", "1");
                    })
                    .catch(function (err) {
                        contentEl.innerHTML = '<p class="reference-answer-error">' + esc(err.message || "请求失败") + "</p>";
                    });
            });
        });

        PracticeModule.init(problems);
    }

    function renderAdvanced(data) {
        var runInfo = $("run-info");
        var rawJson = $("raw-json");
        var meta = data.run_meta || {};
        var modeLabels = { workflow_r1: "\u591a\u6b65\u5de5\u4f5c\u6d41", baseline_qwen3_6: "\u5355\u6b21\u57fa\u7ebf" };
        var modeLabel = modeLabels[meta.mode] || meta.mode || "\u591a\u6b65\u5de5\u4f5c\u6d41";
            var info = "<span>Run ID: " + esc(meta.run_id || "-") + "</span>" +
                "<span>\u6a21\u5f0f: " + esc(modeLabel) + "</span>" +
                "<span>\u6a21\u578b: " + esc(meta.model || "-") + "</span>" +
                "<span>OCR \u6a21\u578b: " + esc(meta.ocr_model || "-") + "</span>" +
                "<span>Provider: " + esc(meta.provider || "-") + "</span>" +
                (meta.started_at ? '<span>\u5f00\u59cb: ' + esc(formatTimestamp(meta.started_at)) + "</span>" : "") +
                (meta.completed_at ? '<span>\u5b8c\u6210: ' + esc(formatTimestamp(meta.completed_at)) + "</span>" : "");
        if (data.uncertainty_flags && data.uncertainty_flags.length) {
            info += '<br><span>Flags: ' + esc(data.uncertainty_flags.join(", ")) + "</span>";
        }
        runInfo.innerHTML = info;
        rawJson.textContent = JSON.stringify(data.raw_output, null, 2);
    }

    /* ===== Chat ===== */
    var _chatRunId = null;
    var _chatSending = false;

    function renderChatMarkdown(element) {
        var text = element.textContent || "";
        if (typeof marked !== "undefined") element.innerHTML = marked.parse(text);
        renderAllMath(element);
    }

    function initChat(runId) {
        _chatRunId = runId;
        var chatSection = $("chat-section");
        var badge = $("chat-run-badge");
        if (badge && runId) { badge.textContent = "Run: " + runId.substring(0, 8); badge.style.display = ""; }
        chatSection.style.display = "";
        loadChatHistory(runId);
    }

    function loadChatHistory(runId) {
        fetch("/chat/history/" + runId, { headers: AuthModule.getAuthHeader() })
            .then(function (resp) {
                if (!resp.ok) return resp.json().then(function (d) { throw new Error(d.error || "HTTP " + resp.status); });
                return resp.json();
            })
            .then(function (data) {
                var container = $("chat-messages");
                container.innerHTML = "";
                (data.messages || []).forEach(function (m) {
                    if (m.role === "user") appendUserBubble(m.content);
                    else appendAssistantBubbleContent(m.content);
                });
                scrollChatToBottom();
            })
            .catch(function () {});
    }

    function sendChatMessage() {
        if (_chatSending || !_chatRunId) return;
        var input = $("chat-input");
        var text = input.value.trim();
        if (!text) return;
        _chatSending = true;
        input.value = "";
        var sendBtn = $("chat-send-btn");
        sendBtn.disabled = true;
        appendUserBubble(text);
        var bubble = appendAssistantBubbleStreaming();
        bubble.innerHTML = '<span class="chat-thinking"><span class="chat-thinking-dot"></span><span class="chat-thinking-dot"></span><span class="chat-thinking-dot"></span></span>';
        scrollChatToBottom();

        var formData = new FormData();
        formData.append("run_id", _chatRunId);
        formData.append("message", text);
        formData.append("model", $("chat-model-select").value);

        fetch("/chat/stream", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData })
            .then(function (resp) {
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                return readChatSSE(resp.body, bubble);
            })
            .catch(function (err) { bubble.textContent = "[\u8bf7\u6c42\u5931\u8d25: " + err.message + "]"; })
            .finally(function () {
                _chatSending = false;
                sendBtn.disabled = false;
                var respEl = bubble.querySelector(".chat-response-content");
                renderChatMarkdown(respEl || bubble);
                scrollChatToBottom();
            });
    }

    function readChatSSE(body, bubble) {
        var reader = body.getReader();
        var decoder = new TextDecoder("utf-8");
        var buffer = "";
        var thinkingBlock = null;
        function removeThinkingDots() { var dots = bubble.querySelector(".chat-thinking"); if (dots) dots.remove(); }
        function ensureResponseEl() {
            var el = bubble.querySelector(".chat-response-content");
            if (!el) { el = document.createElement("div"); el.className = "chat-response-content"; bubble.appendChild(el); }
            return el;
        }
        function readNext() {
            return reader.read().then(function (chunk) {
                if (chunk.done) return;
                buffer += decoder.decode(chunk.value, { stream: true });
                var lines = buffer.split("\n");
                buffer = lines.pop() || "";
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i].trim();
                    if (!line || !line.startsWith("data:")) continue;
                    try {
                        var event = JSON.parse(line.substring(5).trim());
                        if (event.type === "chat_thinking" && event.content) {
                            removeThinkingDots();
                            if (!thinkingBlock) {
                                thinkingBlock = document.createElement("details");
                                thinkingBlock.className = "chat-thinking-block";
                                thinkingBlock.open = true;
                                thinkingBlock.innerHTML = "<summary>\u601d\u8003\u8fc7\u7a0b</summary><div class=\"chat-thinking-content\"></div>";
                                bubble.appendChild(thinkingBlock);
                            }
                            var tc = thinkingBlock.querySelector(".chat-thinking-content");
                            if (tc) tc.textContent += event.content;
                            scrollChatToBottom();
                        } else if (event.type === "chat_chunk" && event.content) {
                            removeThinkingDots();
                            ensureResponseEl().textContent += event.content;
                            scrollChatToBottom();
                        } else if (event.type === "chat_error") {
                            bubble.textContent = "[\u9519\u8bef: " + event.message + "]";
                        }
                    } catch (e) {}
                }
                return readNext();
            });
        }
        return readNext();
    }

    function appendUserBubble(text) {
        var div = document.createElement("div");
        div.className = "chat-bubble chat-bubble-user";
        div.textContent = text;
        $("chat-messages").appendChild(div);
    }

    function appendAssistantBubbleStreaming() {
        var div = document.createElement("div");
        div.className = "chat-bubble chat-bubble-assistant";
        $("chat-messages").appendChild(div);
        return div;
    }

    function appendAssistantBubbleContent(text) {
        var div = document.createElement("div");
        div.className = "chat-bubble chat-bubble-assistant";
        div.textContent = text;
        $("chat-messages").appendChild(div);
        renderChatMarkdown(div);
    }

    function scrollChatToBottom() {
        var container = $("chat-messages");
        container.scrollTop = container.scrollHeight;
    }

    /* ===== Onboarding Module ===== */
    var OnboardingModule = {
        _steps: [
            { target: "#problem-input-group", title: "输入题目", text: "在这里输入数学或物理题目的内容，也可以拍照自动识别。" },
            { target: "#student-solution", title: "输入解答", text: "在这里输入学生的解题过程，每行一步。" },
            { target: "#subject-select", title: "选择学科", text: "选择对应的学科，或使用自动识别功能。" },
            { target: ".depth-control", title: "推理深度", text: "标准模式适合大多数情况，快速模式适合简单题目。" },
            { target: "#run-btn", title: "开始分析", text: "点击此按钮开始分析，系统会逐步验证每个解题步骤。" }
        ],

        init: function () {
            var helpBtn = $("btn-onboarding-help");
            if (helpBtn) helpBtn.addEventListener("click", function () { OnboardingModule.startTour(); });
            document.querySelectorAll(".info-icon").forEach(function (icon) {
                icon.addEventListener("click", function (e) {
                    e.stopPropagation();
                    OnboardingModule._toggleInfoPopup(this);
                });
            });
            if (localStorage.getItem("stem_tutor_onboarding_done")) return;
            setTimeout(function () { OnboardingModule.startTour(); }, 800);
        },

        startTour: function () {
            if (window.location.hash !== "#new") {
                window.location.hash = "#new";
                var self = this;
                setTimeout(function () {
                    if (typeof InputPanel !== "undefined" && InputPanel.expand) InputPanel.expand();
                    self._showStep(0);
                }, 500);
                return;
            }
            if (typeof InputPanel !== "undefined" && InputPanel.expand) InputPanel.expand();
            this._showStep(0);
        },

        _showStep: function (index) {
            var self = this;
            if (index >= this._steps.length) {
                this._removeOverlay();
                localStorage.setItem("stem_tutor_onboarding_done", "1");
                return;
            }
            this._removeOverlay();
            var step = this._steps[index];
            var target = document.querySelector(step.target);
            if (!target) { self._showStep(index + 1); return; }
            if (window.AppRouter && AppRouter.navigate) AppRouter.navigate("new");
            if (typeof InputPanel !== "undefined" && InputPanel.expand) InputPanel.expand();

            var overlay = document.createElement("div");
            overlay.className = "onboarding-overlay";
            overlay.id = "onboarding-overlay";
            document.body.appendChild(overlay);

            target.classList.add("onboarding-highlight");
            target.scrollIntoView({ behavior: "smooth", block: "center" });

            var tooltip = document.createElement("div");
            tooltip.className = "onboarding-tooltip";
            tooltip.innerHTML = '<p class="onboarding-step-indicator">步骤 ' + (index + 1) + '/' + this._steps.length + '</p>' +
                '<div class="onboarding-tooltip-title">' + step.title + '</div>' +
                '<div class="onboarding-tooltip-text">' + step.text + '</div>' +
                '<div class="onboarding-tooltip-actions">' +
                (index > 0 ? '<button type="button" class="onboarding-btn onboarding-btn-prev" data-step="' + (index - 1) + '">上一步</button>' : '<span></span>') +
                (index < this._steps.length - 1
                    ? '<button type="button" class="onboarding-btn onboarding-btn-next" data-step="' + (index + 1) + '">下一步</button>'
                    : '<button type="button" class="onboarding-btn onboarding-btn-done">完成</button>') +
                '</div>';
            document.body.appendChild(tooltip);

            var rect = target.getBoundingClientRect();
            tooltip.style.top = (rect.bottom + window.scrollY + 12) + "px";
            tooltip.style.left = Math.max(10, rect.left + window.scrollX) + "px";

            tooltip.querySelector(index < this._steps.length - 1 ? ".onboarding-btn-next" : ".onboarding-btn-done").addEventListener("click", function () {
                var nextIdx = parseInt(this.getAttribute("data-step"), 10);
                if (isNaN(nextIdx)) nextIdx = self._steps.length;
                target.classList.remove("onboarding-highlight");
                self._showStep(nextIdx);
            });
            var prevBtn = tooltip.querySelector(".onboarding-btn-prev");
            if (prevBtn) {
                prevBtn.addEventListener("click", function () {
                    target.classList.remove("onboarding-highlight");
                    self._showStep(parseInt(this.getAttribute("data-step"), 10));
                });
            }
        },

        _removeOverlay: function () {
            var overlay = $("onboarding-overlay");
            if (overlay) overlay.remove();
            document.querySelectorAll(".onboarding-highlight").forEach(function (el) { el.classList.remove("onboarding-highlight"); });
            document.querySelectorAll(".onboarding-tooltip").forEach(function (el) { el.remove(); });
        },

        _toggleInfoPopup: function (icon) {
            var existing = icon.parentElement.querySelector(".info-popup");
            if (existing) { existing.remove(); return; }
            document.querySelectorAll(".info-popup").forEach(function (p) { p.remove(); });
            var text = icon.getAttribute("data-info") || "";
            if (!text) return;
            var popup = document.createElement("div");
            popup.className = "info-popup";
            popup.textContent = text;
            icon.parentElement.appendChild(popup);
            setTimeout(function () {
                document.addEventListener("click", function handler(e) {
                    if (!popup.contains(e.target) && e.target !== icon) { popup.remove(); document.removeEventListener("click", handler); }
                });
            }, 10);
        }
    };

    /* ===== Export Module ===== */
    var ExportModule = {
        init: function () {
            var toggle = $("export-toggle");
            var menu = $("export-menu");
            if (!toggle || !menu) return;

            toggle.addEventListener("click", function (e) {
                e.stopPropagation();
                menu.classList.toggle("open");
            });
            document.addEventListener("click", function () { menu.classList.remove("open"); });

            var printBtn = $("export-print");
            if (printBtn) printBtn.addEventListener("click", function () { ExportModule._printResult(); });

            var mdBtn = $("export-markdown");
            if (mdBtn) mdBtn.addEventListener("click", function () { ExportModule._copyMarkdown(); });

            var jsonBtn = $("export-json");
            if (jsonBtn) jsonBtn.addEventListener("click", function () { ExportModule._downloadJson(); });
        },

        _printResult: function () {
            $("export-menu").classList.remove("open");
            document.body.classList.add("print-mode");
            setTimeout(function () { window.print(); }, 200);
            setTimeout(function () { document.body.classList.remove("print-mode"); }, 500);
        },

        _copyMarkdown: function () {
            $("export-menu").classList.remove("open");
            if (!_lastResult) return;
            var md = ExportModule._generateMarkdown(_lastResult);
            navigator.clipboard.writeText(md).then(function () {
                ExportModule._showToast("Markdown 已复制到剪贴板");
            }).catch(function () {
                ExportModule._showToast("复制失败，请手动复制");
            });
        },

        _downloadJson: function () {
            $("export-menu").classList.remove("open");
            if (!_lastResult) return;
            var json = JSON.stringify(_lastResult, null, 2);
            var blob = new Blob([json], { type: "application/json" });
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            var runId = (_lastResult.run_meta || {}).run_id || "unknown";
            a.href = url;
            a.download = "analysis-" + runId + ".json";
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
        },

        _generateMarkdown: function (data) {
            var lines = [];
            lines.push("# 分析结果");
            lines.push("");
            if (data.problem_text) { lines.push("## 题目"); lines.push(data.problem_text); lines.push(""); }
            var summary = data.concise_summary;
            if (summary) { lines.push("## 概要"); lines.push(summary); lines.push(""); }
            var ref = data.reference_solution;
            if (ref && ref.reference_text) { lines.push("## 参考解答"); lines.push(ref.reference_text); lines.push(""); }
            if (data.steps && data.steps.length) {
                lines.push("## 步骤验证");
                lines.push("| 步骤 | 内容 | 结果 | 置信度 |");
                lines.push("|------|------|------|--------|");
                data.steps.forEach(function (s) {
                    var label = s.label || "-";
                    var text = (s.raw_text || s.normalized_text || "").replace(/\n/g, " ");
                    lines.push("| " + (s.step_id || "-") + " | " + text + " | " + label + " | " + ((s.confidence || 0) * 100).toFixed(0) + "% |");
                });
                lines.push("");
            }
            if (data.diagnoses && data.diagnoses.length) {
                lines.push("## 错误诊断");
                data.diagnoses.forEach(function (d) {
                    lines.push("- 步骤 " + d.step_id + ": " + (d.error_code || "") + " — " + (d.root_cause_hypothesis || ""));
                });
                lines.push("");
            }
            var feedback = data.final_feedback;
            if (feedback) {
                lines.push("## 学习反馈");
                if (feedback.review_concepts) lines.push("- 复习概念: " + feedback.review_concepts.join(", "));
                if (feedback.next_action) lines.push("- 下一步: " + feedback.next_action);
                lines.push("");
            }
            if (data.review_problems && data.review_problems.length) {
                lines.push("## 复习练习");
                data.review_problems.forEach(function (p, i) {
                    lines.push((i + 1) + ". " + p.problem_text);
                });
            }
            return lines.join("\n");
        },

        _showToast: function (msg) {
            var toast = $("toast");
            if (!toast) return;
            toast.textContent = msg;
            toast.classList.add("show");
            setTimeout(function () { toast.classList.remove("show"); }, 2500);
        }
    };

    /* ===== Practice Module ===== */
    var PracticeModule = {
        _problems: [],
        _submitted: {},

        init: function (problems) {
            this._problems = problems || [];
            this._submitted = {};
        },

        submit: function (index) {
            if (this._submitted[index]) return;
            var problem = this._problems[index];
            if (!problem) return;

            var card = document.querySelector('.review-card[data-review-index="' + index + '"]');
            var textarea = card.querySelector(".practice-input");
            var submitBtn = card.querySelector(".btn-practice-submit");
            var spinnerEl = card.querySelector(".practice-spinner");
            var resultEl = $("practice-result-" + index);

            var studentSolution = textarea.value.trim();
            if (!studentSolution) return;

            submitBtn.disabled = true;
            textarea.disabled = true;
            spinnerEl.style.display = "";

            var subjectId = "calculus";
            if (_lastResult && _lastResult.run_meta && _lastResult.run_meta.subject_id) {
                subjectId = _lastResult.run_meta.subject_id;
            }
            var subjectSelect = $("subject-select");
            if (subjectSelect && subjectSelect.value) {
                subjectId = subjectSelect.value;
            }

            var weaknessCode = problem.weakness_code || problem.difficulty_label || "";

            var formData = new FormData();
            formData.append("problem_text", problem.problem_text);
            formData.append("student_solution", studentSolution);
            formData.append("subject_id", subjectId);
            formData.append("related_weakness_code", weaknessCode);

            var self = this;

            fetch("/practice/verify", { method: "POST", headers: AuthModule.getAuthHeader(), body: formData })
                .then(function (resp) {
                    if (!resp.ok) return resp.text().then(function (t) { throw new Error(t || "HTTP " + resp.status); });
                    return resp;
                })
                .then(function (resp) {
                    return _readPracticeSSE(
                        resp,
                        function (progress) {
                            var detail = progress.detail || progress.message || "";
                            if (detail) spinnerEl.innerHTML = '<span class="spinner-small"></span> ' + esc(detail);
                        },
                        function (result) {
                            spinnerEl.style.display = "none";
                            self._showResult(index, result);
                            if (result.all_correct) {
                                self._submitted[index] = true;
                                submitBtn.disabled = true;
                                textarea.disabled = true;
                            } else {
                                textarea.disabled = false;
                                textarea.value = "";
                                textarea.placeholder = "修改后重新提交...";
                                submitBtn.disabled = true;
                            }
                            if (typeof MasteryModule !== "undefined" && MasteryModule.recordPractice) {
                                MasteryModule.recordPractice(weaknessCode, result.all_correct);
                            }
                        },
                        function (errMsg) {
                            spinnerEl.style.display = "none";
                            resultEl.innerHTML = '<p class="practice-error-text">' + esc(errMsg || "验证失败，请重试。") + "</p>";
                            resultEl.style.display = "";
                            textarea.disabled = false;
                            submitBtn.disabled = false;
                            delete self._submitted[index];
                        }
                    );
                })
                .catch(function (err) {
                    spinnerEl.style.display = "none";
                    resultEl.innerHTML = '<p class="practice-error-text">' + esc(err.message || "网络错误，请重试。") + "</p>";
                    resultEl.style.display = "";
                    textarea.disabled = false;
                    submitBtn.disabled = false;
                    delete self._submitted[index];
                });
        },

        _showResult: function (index, result) {
            var resultEl = $("practice-result-" + index);
            var html = "";

            if (result.all_correct) {
                resultEl.className = "practice-result correct";
                html += '<p class="practice-result-summary">所有步骤正确！做得好！</p>';
            } else {
                resultEl.className = "practice-result incorrect";
                if (result.summary) html += '<p class="practice-result-summary">' + esc(result.summary) + "</p>";
                if (result.hint) html += '<p class="practice-hint">' + esc(result.hint) + "</p>";
                var steps = result.step_results || result.steps;
                if (steps && steps.length) {
                    html += '<div class="practice-steps">';
                    steps.forEach(function (step, si) {
                        var isCorrect = step.label === "correct";
                        var stepClass = isCorrect ? "step-correct" : "step-incorrect";
                        var stepLabel = isCorrect ? "正确" : "有误";
                        html += '<div class="practice-step-result ' + stepClass + '">';
                        html += '<span class="step-label">' + (si + 1) + ". " + stepLabel + "</span>";
                        if (step.text) html += '<span class="step-text">' + esc(step.text) + "</span>";
                        html += "</div>";
                    });
                    html += "</div>";
                }
            }

            resultEl.innerHTML = html;
            resultEl.style.display = "";
            renderAllMath(resultEl);
        }
    };

    function _readPracticeSSE(response, onProgress, onResult, onError) {
        var reader = response.body.getReader();
        var decoder = new TextDecoder("utf-8");
        var buffer = "";

        function readNext() {
            return reader.read().then(function (chunk) {
                if (chunk.done) { onError("连接中断，请重试。"); return; }
                buffer += decoder.decode(chunk.value, { stream: true });
                var lines = buffer.split("\n");
                buffer = lines.pop();
                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    if (line.indexOf("data: ") === 0) {
                        try {
                            var data = JSON.parse(line.substring(6));
                            if (data.type === "progress") onProgress(data);
                            else if (data.type === "result") { onResult(data); return; }
                            else if (data.type === "error") { onError(data.message); return; }
                        } catch (e) {}
                    }
                }
                return readNext();
            });
        }
        return readNext();
    }

    function _readReferenceSSE(response) {
        return new Promise(function (resolve, reject) {
            var reader = response.body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buffer = "";
            var resolved = false;

            function readNext() {
                return reader.read().then(function (chunk) {
                    if (chunk.done) {
                        if (!resolved) reject(new Error("连接中断"));
                        return;
                    }
                    buffer += decoder.decode(chunk.value, { stream: true });
                    var lines = buffer.split("\n");
                    buffer = lines.pop();
                    for (var i = 0; i < lines.length; i++) {
                        var line = lines[i];
                        if (line.indexOf("data: ") === 0) {
                            try {
                                var data = JSON.parse(line.substring(6));
                                if (data.type === "result") {
                                    resolved = true;
                                    resolve(data);
                                    return;
                                }
                            } catch (e) {}
                        }
                    }
                    return readNext();
                });
            }
            readNext().catch(reject);
        });
    }

    /* ===== Mastery Module ===== */
    var _humanizeErrorCode = function (code) {
        var map = {
            CHAIN_RULE_MISUSE: "链式法则误用",
            SUBSTITUTION_MAPPING_MISMATCH: "换元映射错误",
            SIGN_ARITHMETIC_ERROR: "符号/算术错误",
            COEFFICIENT_OMISSION: "系数遗漏",
            FINAL_CALCULATION_ERROR: "最终计算错误",
            DOMAIN_CONDITION_IGNORED: "定义域/条件忽略",
            OBJECT_CONFUSION_LIMIT_DERIVATIVE_INTEGRAL: "导数/积分混淆",
            UNSUPPORTED_JUMP: "无依据跳跃",
            NOTATION_UNCLEAR: "符号不清晰"
        };
        return map[code] || code;
    };

    var MasteryModule = {
        STORAGE_KEY: "stem_tutor_mastery",

        _serverData: null,

        getData: function () {
            if (this._serverData) return this._serverData;
            try { return JSON.parse(localStorage.getItem(this.STORAGE_KEY)) || { errors: {}, practice_history: [] }; }
            catch (e) { return { errors: {}, practice_history: [] }; }
        },

        saveData: function (data) {
            this._serverData = data;
            try { localStorage.setItem(this.STORAGE_KEY, JSON.stringify(data)); }
            catch (e) {}
            fetch("/api/user/mastery", {
                method: "POST",
                headers: Object.assign({ "Content-Type": "application/json" }, AuthModule.getAuthHeader()),
                body: JSON.stringify(data)
            }).catch(function () {});
        },

        _loadPromise: null,

        loadFromServer: function () {
            var self = this;
            if (this._loadPromise) return this._loadPromise;
            this._loadPromise = fetch("/api/user/mastery", { headers: AuthModule.getAuthHeader() })
                .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
                .then(function (data) {
                    if (data && typeof data === "object") {
                        self._serverData = data;
                        localStorage.setItem(self.STORAGE_KEY, JSON.stringify(data));
                    }
                }).catch(function () {})
                .finally(function () { self._loadPromise = null; });
            return this._loadPromise;
        },

        recordEncounter: function (errorCode) {
            if (!errorCode) return;
            var self = this;
            var doRecord = function () {
                var data = self.getData();
                if (!data.errors[errorCode]) data.errors[errorCode] = { total: 0, mastered: false, timestamps: [] };
                data.errors[errorCode].total++;
                data.errors[errorCode].timestamps.push(Date.now());
                self.saveData(data);
            };
            if (this._loadPromise) { this._loadPromise.then(doRecord); } else { doRecord(); }
        },

        toggleMastered: function (errorCode) {
            if (!errorCode) return;
            var self = this;
            var doToggle = function () {
                var data = self.getData();
                if (!data.errors[errorCode]) data.errors[errorCode] = { total: 0, mastered: false, timestamps: [] };
                data.errors[errorCode].mastered = !data.errors[errorCode].mastered;
                self.saveData(data);
            };
            if (this._loadPromise) { this._loadPromise.then(doToggle); } else { doToggle(); }
        },

        recordPractice: function (weaknessCode, correct) {
            if (!weaknessCode) return;
            var data = this.getData();
            data.practice_history = data.practice_history || [];
            data.practice_history.push({ code: weaknessCode, correct: correct, ts: Date.now() });
            if (data.practice_history.length > 200) data.practice_history = data.practice_history.slice(-200);
            if (correct) {
                if (!data.errors[weaknessCode]) data.errors[weaknessCode] = { total: 0, mastered: false, timestamps: [] };
                data.errors[weaknessCode].mastered = true;
            }
            this.saveData(data);
        },

        renderProfile: function () {
            var container = $("stats-panel-profile");
            if (!container) return;
            var data = this.getData();
            var errorKeys = Object.keys(data.errors);
            var emptyEl = container.querySelector(".profile-empty");
            var contentEl = container.querySelector(".profile-content");
            if (!errorKeys.length) {
                if (emptyEl) emptyEl.style.display = "";
                if (contentEl) contentEl.style.display = "none";
                return;
            }
            if (emptyEl) emptyEl.style.display = "none";
            if (contentEl) contentEl.style.display = "";

            var radarCanvas = $("mastery-radar-chart");
            if (radarCanvas && window.Chart) {
                var labels = errorKeys.map(function (k) { return _humanizeErrorCode(k); });
                var totalData = errorKeys.map(function (k) { return data.errors[k].total; });
                var masteredCount = errorKeys.filter(function (k) { return data.errors[k].mastered; }).length;

                if (radarCanvas._chart) radarCanvas._chart.destroy();
                radarCanvas._chart = new Chart(radarCanvas.getContext("2d"), {
                    type: "radar",
                    data: {
                        labels: labels,
                        datasets: [{
                            label: "出现次数",
                            data: totalData,
                            backgroundColor: "rgba(37,99,235,0.15)",
                            borderColor: "rgba(37,99,235,0.8)",
                            borderWidth: 2,
                            pointBackgroundColor: "rgba(37,99,235,1)"
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: { r: { beginAtZero: true, ticks: { stepSize: 1 } } },
                        plugins: { legend: { display: false } }
                    }
                });
            }

            var progressList = container.querySelector(".mastery-progress-list");
            if (progressList) {
                var phtml = "";
                errorKeys.forEach(function (code) {
                    var e = data.errors[code];
                    var human = _humanizeErrorCode(code);
                    var statusText = e.mastered ? "已掌握" : "学习中";
                    var statusClass = e.mastered ? "mastered" : "learning";
                    phtml += '<div class="mastery-item">';
                    phtml += '<div class="mastery-label">' + esc(human) + '</div>';
                    phtml += '<div class="mastery-bar-bg"><div class="mastery-bar-fill ' + statusClass + '" style="width:' + (e.mastered ? 100 : Math.min(e.total * 20, 80)) + '%;"></div></div>';
                    phtml += '<div class="mastery-status ' + statusClass + '">' + statusText + ' (' + e.total + '次)</div>';
                    phtml += "</div>";
                });
                progressList.innerHTML = phtml;
            }

            var milestonesList = container.querySelector(".milestones-list");
            if (milestonesList) {
                var totalEncounters = 0;
                errorKeys.forEach(function (k) { totalEncounters += data.errors[k].total; });
                var totalMastered = masteredCount;
                var totalPractice = (data.practice_history || []).length;
                var correctPractice = (data.practice_history || []).filter(function (p) { return p.correct; }).length;
                var milestones = [
                    { title: "初次诊断", desc: "完成第一次错误诊断", achieved: totalEncounters >= 1 },
                    { title: "五次诊断", desc: "累计 5 次错误诊断", achieved: totalEncounters >= 5 },
                    { title: "首次掌握", desc: "标记第一个概念为已掌握", achieved: totalMastered >= 1 },
                    { title: "练习达人", desc: "完成 10 次练习", achieved: totalPractice >= 10 },
                    { title: "正确率 80%", desc: "练习正确率达到 80%", achieved: totalPractice >= 5 && (correctPractice / totalPractice) >= 0.8 }
                ];
                var mhtml = "";
                milestones.forEach(function (m) {
                    mhtml += '<div class="milestone-item ' + (m.achieved ? "milestone-achieved" : "milestone-locked") + '">';
                    mhtml += '<div class="milestone-icon">' + (m.achieved ? "&#10003;" : "&#128274;") + '</div>';
                    mhtml += '<div class="milestone-info"><div class="milestone-title">' + esc(m.title) + '</div><div class="milestone-desc">' + esc(m.desc) + '</div></div>';
                    mhtml += "</div>";
                });
                milestonesList.innerHTML = mhtml;
            }
        },

        init: function () {
            var tabs = document.querySelectorAll(".stats-tab");
            var panels = document.querySelectorAll(".stats-panel");
            var self = this;
            tabs.forEach(function (tab) {
                tab.addEventListener("click", function () {
                    var target = this.getAttribute("data-stats-tab");
                    tabs.forEach(function (t) { t.classList.remove("active"); });
                    panels.forEach(function (p) { p.classList.remove("active"); p.style.display = "none"; });
                    this.classList.add("active");
                    var panel = $("stats-panel-" + target);
                    if (panel) { panel.classList.add("active"); panel.style.display = ""; }
                    if (target === "profile") {
                        setTimeout(function () { self.renderProfile(); }, 100);
                    }
                });
            });
            this.loadFromServer();
        }
    };

    document.addEventListener("DOMContentLoaded", init);

    var QueueModule = (function() {
        var _pollTimer = null;
        var _currentBatchId = null;
        var _expandedBatchId = null;

        function _stopPoll() {
            if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
        }

        function _startPoll(batchId) {
            _stopPoll();
            _currentBatchId = batchId;
            _pollTimer = setInterval(function() { _refreshStatus(batchId); }, 5000);
        }

        function _api(method, path, body) {
            var token = localStorage.getItem(AuthModule.TOKEN_KEY);
            var opts = { method: method, headers: token ? { "Authorization": "Bearer " + token } : {} };
            if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
            return fetch(path, opts).then(function(r) { return r.json(); });
        }

        function load() {
            _stopPoll();
            _expandedBatchId = null;
            document.getElementById("queue-filter-status").onchange = function() { _loadList(); };
            document.getElementById("queue-new-btn").onclick = function() { _openNewModal(); };
            _loadList();
        }

        function _loadList() {
            var status = document.getElementById("queue-filter-status").value;
            var params = "?per_page=50";
            if (status) params += "&status=" + status;
            _api("GET", "/batch/list" + params).then(function(data) {
                data.batches = (data.batches || []).filter(function(b) { return b.total_count > 1; });
                _renderList(data);
            });
        }

        function _renderList(data) {
            var container = document.getElementById("queue-list");
            if (!data.batches || data.batches.length === 0) {
                container.innerHTML = '<p style="text-align:center;color:var(--text-muted,#888)">暂无批量分析任务</p>';
                return;
            }
            var html = "";
            data.batches.forEach(function(b) {
                var done = b.completed_count + b.failed_count;
                var pct = b.total_count > 0 ? Math.round(done / b.total_count * 100) : 0;
                var settings = typeof b.settings === "string" ? JSON.parse(b.settings) : (b.settings || {});
                var statusClass = "batch-status-" + b.status;
                var statusLabel = {pending:"等待中",running:"运行中",paused:"已暂停",completed:"已完成",cancelled:"已取消"}[b.status] || b.status;
                html += '<div class="batch-card" data-batch-id="' + b.id + '">';
                html += '<div class="batch-card-header">';
                html += '<span class="batch-card-meta">' + (settings.subject_id || "微积分") + ' | ' + (settings.depth || "标准") + ' | ' + (b.created_at || "") + '</span>';
                html += '<span class="batch-status-badge ' + statusClass + '">' + statusLabel + '</span>';
                html += '</div>';
                html += '<div class="batch-card-progress">';
                html += '<div class="batch-progress-bar"><div class="batch-progress-fill" style="width:' + pct + '%"></div></div>';
                html += '<div class="batch-progress-text">' + done + '/' + b.total_count + ' (' + pct + '%)</div>';
                html += '</div>';
                html += '<div class="batch-card-actions">';
                if (b.status === "running") {
                    html += '<button class="btn btn-sm" onclick="QueueModule.pause(\'' + b.id + '\')">暂停</button>';
                    html += '<button class="btn btn-sm" onclick="QueueModule.cancel(\'' + b.id + '\')">取消</button>';
                } else if (b.status === "paused") {
                    html += '<button class="btn btn-sm btn-primary" onclick="QueueModule.resume(\'' + b.id + '\')">继续</button>';
                    html += '<button class="btn btn-sm" onclick="QueueModule.cancel(\'' + b.id + '\')">取消</button>';
                } else if (b.status === "pending") {
                    html += '<button class="btn btn-sm btn-primary" onclick="QueueModule.start(\'' + b.id + '\')">开始</button>';
                    html += '<button class="btn btn-sm" onclick="QueueModule.remove(\'' + b.id + '\')">删除</button>';
                } else {
                    if (b.status === "completed") html += '<button class="btn btn-sm" onclick="QueueModule.viewSummary(\'' + b.id + '\')">查看汇总</button>';
                    html += '<button class="btn btn-sm" onclick="QueueModule.remove(\'' + b.id + '\')">删除</button>';
                }
                html += '<button class="btn btn-sm" onclick="QueueModule.toggleExpand(\'' + b.id + '\')">' + (_expandedBatchId === b.id ? '收起' : '展开详情') + '</button>';
                html += '</div>';
                if (_expandedBatchId === b.id) {
                    html += '<div class="batch-item-list" id="batch-items-' + b.id + '">加载中...</div>';
                }
                html += '</div>';
            });
            container.innerHTML = html;
            if (_expandedBatchId) {
                var expBatch = data.batches.find(function(b) { return b.id === _expandedBatchId; });
                if (expBatch && (expBatch.status === "running" || expBatch.status === "pending")) {
                    _refreshStatus(_expandedBatchId);
                    if (!_pollTimer) _startPoll(_expandedBatchId);
                } else if (expBatch) {
                    _loadBatchItems(_expandedBatchId);
                }
            }
            data.batches.forEach(function(b) {
                if (b.status === "running" && b.id !== _currentBatchId && !_pollTimer) _startPoll(b.id);
            });
        }

        function _renderItemsHtml(items) {
            var html = "";
            (items || []).forEach(function(item) {
                var icon = {pending:"⏳",running:"🔄",completed:"✅",failed:"❌",cancelled:"🚫"}[item.status] || "⏳";
                html += '<div class="batch-item-row">';
                html += '<span class="batch-item-seq">#' + (item.seq + 1) + '</span>';
                html += '<span>' + icon + '</span>';
                if (item.status === "completed" && item.run_id) {
                    html += '<a class="batch-item-link" onclick="QueueModule.viewRun(\'' + item.run_id + '\')">' + esc(item.problem_preview) + '</a>';
                } else {
                    html += '<span class="batch-item-preview">' + esc(item.problem_preview) + '</span>';
                }
                if (item.error_message) html += '<span style="color:#ff6b6b;font-size:12px">(' + esc(item.error_message) + ')</span>';
                html += '</div>';
            });
            return html;
        }

        function _loadBatchItems(batchId) {
            _api("GET", "/batch/" + batchId + "/status").then(function(data) {
                var el = document.getElementById("batch-items-" + batchId);
                if (!el) return;
                el.innerHTML = _renderItemsHtml(data.items) || '<p style="color:var(--text-muted,#888)">暂无题目</p>';
            });
        }

        function _refreshStatus(batchId) {
            _api("GET", "/batch/" + batchId + "/status").then(function(data) {
                var el = document.getElementById("batch-items-" + batchId);
                if (!el) return;
                el.innerHTML = _renderItemsHtml(data.items) || '<p style="color:var(--text-muted,#888)">暂无题目</p>';
                if (data.status !== "running" && data.status !== "pending") {
                    if (_currentBatchId === batchId) _stopPoll();
                }
            });
        }

        function toggleExpand(batchId) {
            _expandedBatchId = _expandedBatchId === batchId ? null : batchId;
            _loadList();
        }

        function pause(batchId) { _api("POST", "/batch/" + batchId + "/pause").then(function() { _loadList(); }); }
        function resume(batchId) { _api("POST", "/batch/" + batchId + "/resume").then(function() { _stopPoll(); _startPoll(batchId); _loadList(); }); }
        function cancel(batchId) { if (confirm("确定取消此批次？")) _api("POST", "/batch/" + batchId + "/cancel").then(function() { _loadList(); }); }
        function start(batchId) { _api("POST", "/batch/" + batchId + "/resume").then(function() { _startPoll(batchId); _loadList(); }); }
        function remove(batchId) { if (confirm("确定删除此批次？")) _api("DELETE", "/batch/" + batchId).then(function() { _loadList(); }); }

        function viewRun(runId) {
            HistoryModule.viewResult(runId);
        }

        function viewSummary(batchId) {
            _expandedBatchId = batchId;
            _loadList();
        }

        function _openNewModal() {
            document.getElementById("batch-modal").style.display = "flex";
            document.getElementById("batch-items-list").innerHTML = "";
            document.getElementById("batch-item-count").textContent = "(0 题)";
            document.getElementById("batch-submit-btn").disabled = true;
            _addItem();
            document.getElementById("batch-modal-close").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
            document.querySelector("#batch-modal .modal-overlay").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
            document.getElementById("batch-cancel-btn").onclick = function() { document.getElementById("batch-modal").style.display = "none"; };
            document.getElementById("batch-add-item-btn").onclick = function() { _addItem(); };
            document.getElementById("batch-submit-btn").onclick = function() { _submitBatch(); };
        }

        function _addItem() {
            var list = document.getElementById("batch-items-list");
            var idx = list.children.length;
            var div = document.createElement("div");
            div.className = "batch-item-input";
            div.innerHTML =
                '<div class="batch-item-input-header"><span class="batch-item-toggle" title="展开/折叠">▼ 第 ' + (idx + 1) + ' 题</span><button class="batch-item-remove" title="删除">&times;</button></div>' +
                '<div class="batch-item-body">' +
                '<label style="font-size:12px;color:var(--text-muted,#888)">题目</label>' +
                '<textarea class="batch-problem-text" placeholder="输入题目文本" rows="2"></textarea>' +
                '<div class="ocr-math-preview is-empty batch-ocr-preview" data-field="problem" data-placeholder="公式预览"></div>' +
                '<div class="upload-area upload-area-small batch-upload-zone" data-field="problem">' +
                '<div class="upload-actions">' +
                '<label class="upload-btn upload-btn-camera">拍照<input type="file" accept="image/*" capture="environment" class="batch-file-input"></label>' +
                '<label class="upload-btn upload-btn-album">选择图片<input type="file" accept="image/*" class="batch-file-input"></label>' +
                '</div><div class="batch-upload-hint" style="font-size:11px;margin-top:4px">或将图片拖拽到此处</div></div>' +
                '<label style="font-size:12px;color:var(--text-muted,#888);margin-top:6px;display:block">学生解答</label>' +
                '<textarea class="batch-solution-text" placeholder="输入学生解题步骤" rows="3"></textarea>' +
                '<div class="ocr-math-preview is-empty batch-ocr-preview" data-field="solution" data-placeholder="公式预览"></div>' +
                '<div class="upload-area upload-area-small batch-upload-zone" data-field="solution">' +
                '<div class="upload-actions">' +
                '<label class="upload-btn upload-btn-camera">拍照<input type="file" accept="image/*" capture="environment" class="batch-file-input"></label>' +
                '<label class="upload-btn upload-btn-album">选择图片<input type="file" accept="image/*" class="batch-file-input"></label>' +
                '</div><div class="batch-upload-hint" style="font-size:11px;margin-top:4px">或将图片拖拽到此处</div></div>' +
                '</div>';
            div.querySelector(".batch-item-remove").onclick = function() { div.remove(); _renumber(); };
            div.querySelector(".batch-item-toggle").onclick = function() {
                var body = div.querySelector(".batch-item-body");
                var collapsed = body.classList.toggle("collapsed");
                this.textContent = (collapsed ? "▶ " : "▼ ") + this.textContent.slice(2);
            };
            var zones = div.querySelectorAll(".batch-upload-zone");
            var inputs = div.querySelectorAll(".batch-file-input");
            function wireZone(zone, input, field) {
                input.addEventListener("change", function() { if (this.files[0]) _ocrWithCrop(this.files[0], zone, field); });
                zone.addEventListener("dragover", function(e) { e.preventDefault(); zone.style.borderColor = "var(--accent)"; });
                zone.addEventListener("dragleave", function() { zone.style.borderColor = ""; });
                zone.addEventListener("drop", function(e) {
                    e.preventDefault(); zone.style.borderColor = "";
                    if (e.dataTransfer.files.length) _ocrWithCrop(e.dataTransfer.files[0], zone, field);
                });
            }
            wireZone(zones[0], inputs[0], "problem");
            wireZone(zones[0], inputs[1], "problem");
            wireZone(zones[1], inputs[2], "solution");
            wireZone(zones[1], inputs[3], "solution");
            var previews = div.querySelectorAll(".batch-ocr-preview");
            div.querySelector(".batch-problem-text").addEventListener("input", function() {
                debounceRenderOcrPreview("batch-problem-" + idx, this.value, previews[0]);
            });
            div.querySelector(".batch-solution-text").addEventListener("input", function() {
                debounceRenderOcrPreview("batch-solution-" + idx, this.value, previews[1]);
            });
            list.appendChild(div);
            _updateCount();
        }

        function _renumber() {
            var items = document.querySelectorAll("#batch-items-list .batch-item-input");
            items.forEach(function(el, i) {
                var toggle = el.querySelector(".batch-item-toggle");
                var arrow = toggle.textContent.charAt(0);
                toggle.textContent = arrow + " 第 " + (i + 1) + " 题";
            });
            _updateCount();
        }

        function _updateCount() {
            var n = document.querySelectorAll("#batch-items-list .batch-item-input").length;
            document.getElementById("batch-item-count").textContent = "(" + n + " 题)";
            document.getElementById("batch-submit-btn").disabled = n === 0;
        }

        function _ocrWithCrop(file, zone, field) {
            var model = document.getElementById("batch-model").value;
            var hint = zone.querySelector(".batch-upload-hint");
            if (hint) hint.textContent = "裁剪中...";
            openCropModal(file, function(croppedFile) {
                if (hint) hint.textContent = "识别中...";
                callOcr(croppedFile, model,
                    function(r) {
                        var text = r.text || "";
                        var itemDiv = zone.closest(".batch-item-input");
                        var textarea = field === "problem" ? itemDiv.querySelector(".batch-problem-text") : itemDiv.querySelector(".batch-solution-text");
                        textarea.value = text;
                        var preview = itemDiv.querySelector('.batch-ocr-preview[data-field="' + field + '"]');
                        if (preview) renderOcrPreview(text, preview);
                        if (hint) hint.textContent = "识别完成";
                        setTimeout(function() { if (hint) hint.textContent = "或将图片拖拽到此处"; }, 1500);
                    },
                    function() {
                        if (hint) hint.textContent = "识别失败，请重试";
                        setTimeout(function() { if (hint) hint.textContent = "或将图片拖拽到此处"; }, 3000);
                    }
                );
            });
        }

        function _submitBatch() {
            var items = [];
            document.querySelectorAll("#batch-items-list .batch-item-input").forEach(function(el) {
                var pt = el.querySelector(".batch-problem-text").value.trim();
                var st = el.querySelector(".batch-solution-text").value.trim();
                if (pt) items.push({ problem_text: pt, student_solution: st, source_type: "text" });
            });
            if (items.length === 0) { alert("至少需要一道题目"); return; }
            var settings = {
                model: document.getElementById("batch-model").value,
                subject_id: document.getElementById("batch-subject").value,
                mode: document.getElementById("batch-mode").value,
                depth: document.getElementById("batch-depth").value,
            };
            document.getElementById("batch-submit-btn").disabled = true;
            document.getElementById("batch-submit-btn").textContent = "提交中...";
            _api("POST", "/batch/create", { items: items, settings: settings, auto_start: true }).then(function(data) {
                document.getElementById("batch-modal").style.display = "none";
                document.getElementById("batch-submit-btn").textContent = "提交批量分析";
                _loadList();
                if (data.batch_id) _startPoll(data.batch_id);
            }).catch(function() {
                document.getElementById("batch-submit-btn").disabled = false;
                document.getElementById("batch-submit-btn").textContent = "提交批量分析";
            });
        }

        return {
            load: load,
            toggleExpand: toggleExpand,
            pause: pause,
            resume: resume,
            cancel: cancel,
            start: start,
            remove: remove,
            viewRun: viewRun,
            viewSummary: viewSummary,
            _ocrWithCrop: _ocrWithCrop,
        };
    })();
    window.QueueModule = QueueModule;
})();
