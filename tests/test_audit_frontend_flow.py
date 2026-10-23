"""前端界面运行流程审计契约（静态源码断言）。

覆盖 2026-10-18 审计中「文件选择 → job.start → 进度事件 → job.get → 结果渲染 →
原文核对 → 导出 → 失败/取消/重试恢复」链路上的已修复缺陷。每条断言对应一处
具体修复；只读取源码，不启动桌面端或 sidecar。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


# ---------------------------------------------------------------------------
# 进度事件与任务状态机
# ---------------------------------------------------------------------------


def test_progress_events_require_active_job_and_skip_finished_job() -> None:
    main = _source("src/main.ts")
    handler = _section(main, "client.onEvent((event) => {", "async function boot()")

    assert 'if (event.event === "sidecar.fatal")' in handler
    assert "if (!state.jobId || event.job_id !== state.jobId) return;" in handler
    assert "if (completedJobId === event.job_id) return;" in handler
    # 事件过滤必须早于阶段写入，否则清空文件或收尾后的迟到事件会改写 stage/statusText。
    assert handler.index('if (event.event === "sidecar.fatal")') < handler.index(
        "if (!state.jobId || event.job_id !== state.jobId) return;"
    )
    assert handler.index("if (completedJobId === event.job_id) return;") < handler.index(
        "state.stage = cancellationPending"
    )


def test_removing_files_while_busy_cannot_leave_ui_without_actions() -> None:
    main = _source("src/main.ts")
    remove_block = _section(
        main,
        'const remove = target.closest<HTMLButtonElement>("[data-remove]");',
        "const confirm = target.closest",
    )

    assert "if (state.busy) {" in remove_block
    assert "正在处理课表，完成后再调整文件。" in remove_block
    # 处理中必须先拦截；文件列表变化统一走复位函数，不能留下旧任务引用或旧阶段。
    assert remove_block.index("if (state.busy) {") < remove_block.index("resetForNewInput(")


def test_cancelled_file_dialog_keeps_current_result() -> None:
    main = _source("src/main.ts")
    choose = _section(main, "async function chooseFiles()", "async function parseSchedules()")

    assert "if (!paths.length) return;" in choose
    assert choose.index("if (!paths.length) return;") < choose.index("resetForNewInput(")


def test_new_parse_drops_old_job_and_retry_preserves_it_until_started() -> None:
    main = _source("src/main.ts")
    parse = _section(main, "async function parseSchedules()", "async function settleCompletedJob")
    retry = _section(main, "async function retryFailed()", "async function exportExcel()")

    assert 'state.jobId = "";' in parse
    assert parse.index('state.jobId = "";') < parse.index('"job.start"')
    assert 'state.stage = "failed";' in parse

    assert "const previousJobId = state.jobId;" in retry
    assert "job_id: previousJobId" in retry
    assert retry.index('state.jobId = started.job_id;') < retry.index('state.result = null;')
    assert 'state.stage = state.jobId === previousJobId ? previousStage : "failed";' in retry


def test_retry_entry_appears_for_partial_failures_too() -> None:
    main = _source("src/main.ts")

    assert 'state.failedFileCount = job.files.filter((file) => file.status === "failed").length;' in main
    assert 'state.failedFileCount > 0 || ["failed", "interrupted", "cancelled"].includes(state.stage)' in main
    assert "retry.hidden = !canRetry;" in main


# ---------------------------------------------------------------------------
# 请求/响应关联与陈旧结果
# ---------------------------------------------------------------------------


def test_stale_job_responses_cannot_overwrite_newer_task() -> None:
    main = _source("src/main.ts")

    for start, end in (
        ("async function applyReview(", "async function confirmIssue("),
        ("async function confirmIssue(", "async function refreshJobOutput("),
        ("async function refreshJobOutput(", "const render = createFrameScheduler"),
    ):
        body = _section(main, start, end)
        assert "const jobId = state.jobId;" in body
        assert "if (state.jobId !== jobId) return;" in body

    finish = _section(main, "async function finishCompletedJob(", "function showReviewLoadFailure(")
    assert 'client.request<ReviewPayload>("review.get", { job_id: jobId }' in finish
    assert "if (state.jobId !== jobId) return;" in finish


def test_review_load_failure_is_visible_and_retryable() -> None:
    main = _source("src/main.ts")

    assert "function showReviewLoadFailure(message: string)" in main
    assert 'data-reload-review>重新载入原文</button>' in main
    assert "async function reloadReview()" in main
    assert 'const reload = target.closest<HTMLButtonElement>("[data-reload-review]");' in main
    assert "showReviewLoadFailure(formatError(error));" in main


def test_export_is_bound_to_the_visible_result() -> None:
    main = _source("src/main.ts")
    export_body = _section(main, "async function exportExcel()", "async function repairResources()")

    assert "if (state.busy) return;" in export_body
    assert "const result = state.result;" in export_body
    assert 'if (state.result !== result) return setStatus("结果已更新，请重新确认后再导出。");' in export_body
    assert "result_ref: result.result_ref," in export_body
    assert "result_ref: state.result.result_ref" not in main


def test_reentrant_actions_are_blocked_while_busy() -> None:
    main = _source("src/main.ts")

    for start, end in (
        ("async function chooseFiles()", "async function parseSchedules()"),
        ("async function parseSchedules()", "async function settleCompletedJob"),
        ("async function exportExcel()", "async function repairResources()"),
        ("async function repairResources()", "async function clearLocalData()"),
    ):
        body = _section(main, start, end)
        assert "if (state.busy) return;" in body

    assert 'if (state.busy) return;\n    void saveSettings().catch(() => undefined);' in main


# ---------------------------------------------------------------------------
# 分页、滚动与周视图状态
# ---------------------------------------------------------------------------


def test_availability_pagination_resets_only_when_result_changes() -> None:
    main = _source("src/main.ts")

    assert 'const previousResultRef = renderedResult?.result_ref || "";' in main
    assert "if (!result || result.result_ref !== previousResultRef) availabilityPage = 0;" in main
    assert "if (renderedResult !== result) {\n    const previousResultRef" in main


def test_table_scroll_and_calendar_week_survive_same_result_refresh() -> None:
    main = _source("src/main.ts")
    calendar = _source("src/calendarView.ts")

    assert "function captureTableScroll()" in main
    assert "function restoreTableScroll(positions: Map<Tab, number>)" in main
    assert "const scrollPositions = refreshingSameResult ? captureTableScroll() : null;" in main
    assert "if (scrollPositions) restoreTableScroll(scrollPositions);" in main
    assert "let calendarViewDate: Date | null = null;" in main
    assert "const initialDate = calendar?.getDate() || calendarViewDate || undefined;" in main
    assert "initialDate?: Date" in calendar
    assert "initialDate: initialDate ?? firstDate," in calendar


def test_export_button_disabled_state_matches_export_gate() -> None:
    main = _source("src/main.ts")
    export_body = _section(main, "async function exportExcel()", "async function repairResources()")

    assert 'setDisabled("#exportExcel", !result?.can_export || state.busy);' in main
    assert "if (!result.can_export) {" in export_body
    assert 'state.activeTab = "issues";' in export_body
    assert "当前结果还不能导出，请先核对或修正“待处理”中的内容。" in export_body


def test_pager_announces_page_and_restores_keyboard_focus() -> None:
    main = _source("src/main.ts")
    pager_block = _section(
        main,
        'const pager = target.closest<HTMLButtonElement>("[data-availability-page]");',
        "appRoot.addEventListener(\"keydown\"",
    )

    assert '<span aria-live="polite">共 ${total} 个时段' in main
    assert "const goForward = nextPage > previousPage;" in pager_block
    assert "const groupIndex = pagerGroup ? Math.max(0, groups.indexOf(pagerGroup)) : 0;" in pager_block
    assert ':not(:disabled)`);' in pager_block
    assert 'const fallback = group?.querySelector<HTMLButtonElement>("button:not(:disabled)");' in pager_block
    assert 'if (!result || result.result_ref !== previousResultRef) availabilityPage = 0;' in main


# ---------------------------------------------------------------------------
# sidecar NDJSON 请求生命周期
# ---------------------------------------------------------------------------


def test_sidecar_request_write_failure_settles_pending_request() -> None:
    source = _source("src/sidecarClient.ts")
    request_body = _section(source, "async request<T>(command: string", "async forceRestart()")

    assert "const REQUEST_TIMEOUT_MS = 10 * 60 * 1000;" in source
    assert "try {" in request_body
    assert "await child.write(" in request_body
    assert "rejectRequest(normalized);" in request_body
    assert "window.clearTimeout(pending.timer);" in request_body
    # 超时回调也要先确认请求仍在等待，避免响应已到达后重复结束请求。
    assert "const timedOut = this.pending.get(id);" in request_body


def test_sidecar_ignores_closed_child_that_was_replaced() -> None:
    source = _source("src/sidecarClient.ts")

    assert "private onClosed(command: Command<string>)" in source
    assert "private onSpawnError(command: Command<string>, error: unknown)" in source
    assert source.count("if (this.command !== command) return;") >= 2
    assert 'command.on("close", () => this.onClosed(command));' in source
    assert 'command.on("error", (error) => this.onSpawnError(command, error));' in source
    assert "if (this.command === command) this.consume(chunk);" in source
    # 旧进程写了一半的 NDJSON 行不能拼到新进程首条响应上。
    assert source.count('this.buffer = "";') >= 4
    assert "this.rejectAll(new Error(\"本地识别功能正在重新启动，请稍后重试。\"));" in source


def test_sidecar_restart_failure_is_not_unhandled() -> None:
    source = _source("src/sidecarClient.ts")

    assert 'this.start().catch((error) => console.warn("konggu-worker 自动重启失败:", error));' in source
    assert "void this.start();" not in source


# ---------------------------------------------------------------------------
# 原文核对：PDF 生命周期与转义
# ---------------------------------------------------------------------------


def test_review_pdf_document_is_released_when_destroyed_during_load() -> None:
    review = _source("src/reviewView.ts")

    assert "let loadVersion = 0;" in review
    assert "const version = ++loadVersion;" in review
    assert "if (destroyed || version !== loadVersion) {" in review
    assert "void destroyDocument.call(loadedPdf);" in review
    assert "loadVersion += 1;" in review


def test_review_render_failures_stay_visible_and_do_not_escape() -> None:
    review = _source("src/reviewView.ts")

    assert review.count("pageLabelFailure();") >= 2
    assert "第 ${currentPage} 页暂时无法显示" in review
    # 只允许内部类型错误重抛；业务错误必须留在本地提示，不得逃逸成未处理 rejection。
    assert review.count("throw error;") == 1
    assert "if (error instanceof TypeError) throw error;" in review


def test_review_load_failure_is_reported_in_viewer() -> None:
    review = _source("src/reviewView.ts")

    assert "function reportLoadFailure()" in review
    assert "原文暂时无法载入，请确认文件仍在这台电脑上" in review
    assert ".catch(reportLoadFailure)" in review
    assert review.count("reportLoadFailure();") >= 2


def test_review_correction_fields_and_severity_are_escaped() -> None:
    review = _source("src/reviewView.ts")
    main = _source("src/main.ts")

    assert 'escapeHtml(String(activeBlock.week ?? ""))' in review
    assert 'escapeHtml((activeBlock.periods || []).join(","))' in review
    assert 'class="issueCard ${escapeHtml(state)}"' in review
    assert 'class="${escapeHtml(issue.confirmed ? "confirmed" : issue.severity)}"' in main


def test_review_correction_save_is_deduplicated() -> None:
    review = _source("src/reviewView.ts")

    assert "if (button?.disabled) return;" in review
    assert "if (button) button.disabled = true;" in review
    assert "if (current) current.disabled = false;" in review


def test_calendar_reverts_are_guarded_when_view_is_rebuilt() -> None:
    calendar = _source("src/calendarView.ts")

    assert "function revertSafely(arg: EventDropArg | EventResizeDoneArg)" in calendar
    # 跨天、超出教学周、无法吸附节次、保存失败都会回滚，且回滚本身受保护。
    assert calendar.count("revertSafely(arg)") >= 4
    assert calendar.count("arg.revert();") == 1
