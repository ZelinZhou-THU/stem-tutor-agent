(function () {
    "use strict";

    var SITE_TITLE = "\u6570\u7406\u57fa\u7840\u8bfe\u7a0b\u5b66\u4e60\u8f85\u5bfc\u7cfb\u7edf";

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
        return { cls: "status-running", text: "处理中" };
    }

    /* ===== Router ===== */
    var AppRouter = {
        pages: ["new", "history", "stats", "report", "logs", "settings"],
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
                var page = location.hash.replace("#", "") || "new";
                if (self.pages.indexOf(page) >= 0 && page !== self.currentPage) {
                    self.navigate(page, true);
                }
            });
            var hashPage = location.hash.replace("#", "");
            if (hashPage && this.pages.indexOf(hashPage) >= 0) {
                this.navigate(hashPage, true);
            }
        },

        navigate: function (page, skipHash) {
            if (!page || this.pages.indexOf(page) < 0) page = "new";

            document.querySelectorAll(".page").forEach(function (el) {
                el.classList.remove("active");
            });

            var target = $("page-" + page);
            if (target) target.classList.add("active");

            document.querySelectorAll(".nav-item").forEach(function (el) {
                el.classList.toggle("active", el.getAttribute("data-page") === page);
            });

            var titles = {
                new: "\u65b0\u5efa\u8bca\u65ad",
                history: "\u5386\u53f2\u8bb0\u5f55",
                stats: "\u7edf\u8ba1\u6982\u89c8",
                report: "\u5b66\u4e60\u62a5\u544a",
                logs: "\u8c03\u8bd5\u65e5\u5fd7",
                settings: "\u8bbe\u7f6e"
            };
            var titleEl = $("page-title");
            if (titleEl) titleEl.textContent = titles[page] || page;
            document.title = (titles[page] || "") + " - " + SITE_TITLE;

            if (!skipHash) location.hash = page;
            this.currentPage = page;

            if (page === "history") HistoryModule.load();
            if (page === "stats") StatsModule.load();
            if (page === "report") ReportModule.init();
            if (page === "settings") SettingsModule.loadValues();
            if (page === "logs") LogsModule.init();
        },

        _closeMobileSidebar: function () {
            var sidebar = $("sidebar");
            var overlay = $("overlay");
            if (sidebar) sidebar.classList.remove("open");
            if (overlay) overlay.classList.remove("active");
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
            fetch("/history?per_page=100")
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
            fetch("/analyze/result/" + runId)
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

            fetch("/history?" + params.join("&"))
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
                                headers: { "Content-Type": "application/json" },
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
                        headers: { "Content-Type": "application/json" },
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
                    fetch("/api/runs/cleanup?before_days=" + days, { method: "POST" })
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

            fetch("/analyze/result/" + runId)
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
            fetch("/analyze/result/" + runId)
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
            fetch("/stats")
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
            fetch("/report/runs")
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
            fetch("/report/data?" + qs)
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

            fetch("/report/generate", { method: "POST", body: formData, signal: AbortSignal.timeout(600000) })
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

            fetch("/report/list?page=" + page + "&per_page=10")
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

            fetch("/report/" + reportId)
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
            fetch("/report/" + reportId, { method: "DELETE" })
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
            defaultDepth: "standard",
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
        },

        get: function (key) {
            return this.settings[key] !== undefined ? this.settings[key] : this.defaults[key];
        },

        set: function (key, value) {
            this.settings[key] = value;
            localStorage.setItem("stem_tutor_settings", JSON.stringify(this.settings));
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

            var exportBtn = $("btn-export-data");
            if (exportBtn) {
                exportBtn.addEventListener("click", function () {
                    fetch("/history?per_page=9999")
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
        fetch("/ocr", { method: "POST", body: fd })
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
            notice.textContent = "\u63d0\u793a\uff1aKimi-K2.5 \u8bc6\u522b\u5931\u8d25\uff0c\u5df2\u81ea\u52a8\u5207\u6362\u81f3 " + fallbackModel + " \u6a21\u578b\u3002";
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

    /* ===== Main Init ===== */
    function init() {
        AppRouter.init();
        SettingsModule.init();
        InputPanel.init();
        initMobileSidebar();

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

        var cropperInstance = null;
        var cropCallback = null;

        function openCropModal(file, callback) {
            cropCallback = callback;
            var reader = new FileReader();
            reader.onload = function (e) {
                $("crop-image").src = e.target.result;
                $("crop-modal").style.display = "flex";
                if (cropperInstance) cropperInstance.destroy();
                cropperInstance = new Cropper($("crop-image"), {
                    viewMode: 1, dragMode: "move", autoCropArea: 0.9, responsive: true,
                });
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
            quick: "\u5feb\u901f\u6a21\u5f0f\uff1a\u53c2\u8003\u89e3\u7b54\u4ec5\u7528 LLM \u63a8\u7406\uff0c\u9a8c\u8bc1\u4f7f\u7528 SymPy + \u6570\u503c\u6821\u9a8c",
            standard: "\u6807\u51c6\u6a21\u5f0f\uff1a\u5b8c\u6574\u7b56\u7565\u94fe\uff0c\u53cc\u8f6e\u5de5\u5177\u9a8c\u8bc1",
            thorough: "\u6df1\u5ea6\u6a21\u5f0f\uff1a\u5de5\u5177\u8c03\u7528\u6700\u591a 3 \u8f6e\uff0c\u4e32\u884c\u8de8\u6b65\u9a8c\u8bc1\uff0c\u8d85\u65f6\u5bb9\u5fcd\u5ea6\u66f4\u9ad8"
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
                    headers: { "Content-Type": "application/x-www-form-urlencoded" },
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
            startStreamWithReconnect(formData);
        });

        var currentRunId = null;
        var reconnectTimer = null;

        function startStreamWithReconnect(formData) {
            currentRunId = null;
            if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
            fetch("/analyze/stream", { method: "POST", body: formData })
                .then(function (resp) {
                    if (!resp.ok) {
                        return resp.json().catch(function () { return {}; }).then(function (err) {
                            throw new Error(err.error || "HTTP " + resp.status);
                        });
                    }
                    return readSSEStream(resp.body);
                })
                .then(function (data) { renderResults(data); })
                .catch(function (err) {
                    if (currentRunId) { showReconnectingUI(currentRunId); }
                    else { showError("\u8bf7\u6c42\u5931\u8d25\uff1a" + err.message); }
                })
                .finally(function () { hideLoading(); });
        }

        function loadResultAndRender(runId) {
            return fetch("/analyze/result/" + runId)
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
            var pollInterval = 3000;
            var maxPolls = 120;
            var pollCount = 0;
            function pollStatus() {
                if (pollCount >= maxPolls) { showError("\u67e5\u8be2\u7ed3\u679c\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5\u3002"); hideLoading(); return; }
                pollCount++;
                fetch("/analyze/status/" + runId)
                    .then(function (resp) { return resp.json(); })
                    .then(function (data) {
                        var status = normalizeRunStatus(data.user_status || data.status);
                        if (status === "complete" || status === "needs_review" || status === "unavailable") {
                            loadResultAndRender(runId).catch(function (err) {
                                showError((err && err.message) || data.message || "\u83b7\u53d6\u7ed3\u679c\u5931\u8d25\u3002");
                                hideLoading();
                            });
                        } else {
                            var statusEl = loadingDiv.querySelector(".loading-status");
                            if (statusEl) {
                                statusEl.style.display = "";
                                statusEl.textContent = "\u8fde\u63a5\u65ad\u5f00\uff0c\u540e\u7aef\u4ecd\u5728\u8fd0\u884c...\uff08\u5df2\u8f6e\u8be2 " + pollCount + " \u6b21\uff09";
                            }
                            reconnectTimer = setTimeout(pollStatus, pollInterval);
                        }
                    })
                    .catch(function () { reconnectTimer = setTimeout(pollStatus, pollInterval); });
            }
            pollStatus();
        }

        function readSSEStream(body) {
            var reader = body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buffer = "";
            var resultData = null;

            function readNext() {
                return reader.read().then(function (chunk) {
                    if (chunk.done) {
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
                var icon = el.querySelector(".loading-step-icon");
                if (icon) icon.innerHTML = "\u2610";
                var detailEl = el.querySelector(".loading-step-detail");
                if (detailEl) { detailEl.textContent = ""; detailEl.style.display = "none"; }
            });
        }

        function showLoading() {
            runBtn.disabled = true;
            btnText.style.display = "none";
            btnLoading.style.display = "";
            loadingDiv.style.display = "";
            resultsDiv.style.display = "none";
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
            $("chat-section").style.display = "none";
            $("chat-messages").innerHTML = "";
            _chatRunId = null;
        }

        function hideLoading() {
            runBtn.disabled = false;
            btnText.style.display = "";
            btnLoading.style.display = "none";
            loadingDiv.style.display = "none";
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

        fetch("/api/verify-step", { method: "POST", body: formData })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    fetch("/analyze/result/" + runId)
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
            html += '<p class="diagnosis-step">\u6b65\u9aa4 ' + esc(d.step_id) + "</p>";
            html += '<span class="diagnosis-code">' + esc(d.error_code) + "</span>";
            if (d.short_desc) html += " " + esc(d.short_desc);
            html += '<p><span class="field-label">\u7c7b\u522b\uff1a</span>' + esc(d.category) + "</p>";
            html += '<p><span class="field-label">\u5047\u8bbe\u6839\u56e0\uff1a</span>' + esc(d.root_cause_hypothesis) + "</p>";
            html += '<p><span class="field-label">\u652f\u6301\u8bc1\u636e\uff1a</span>' + esc(d.supporting_evidence) + "</p>";
            html += '<p><span class="field-label">\u7f6e\u4fe1\u5ea6\uff1a</span>' + (d.confidence * 100).toFixed(0) + "%</p>";
            html += "</div>";
        });
        container.innerHTML = html;
        section.style.display = "";
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
            html += '<div class="review-card">';
            html += '<p class="review-text">(' + (i + 1) + ") " + esc(p.problem_text) + "</p>";
            html += '<p class="review-meta">' + (diff ? '<span class="difficulty-badge">' + esc(diff) + "</span> " : "") + esc(p.rationale) + "</p>";
            html += "</div>";
        });
        container.innerHTML = html;
        section.style.display = "";
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
        fetch("/chat/history/" + runId)
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

        fetch("/chat/stream", { method: "POST", body: formData })
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

    document.addEventListener("DOMContentLoaded", init);
})();
