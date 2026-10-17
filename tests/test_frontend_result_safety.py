from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _function_body(main: str, signature: str, next_marker: str) -> str:
    return main.split(signature, 1)[1].split(next_marker, 1)[0]


def test_availability_results_are_paged_instead_of_silently_truncated() -> None:
    main = _source("src/main.ts")

    assert "slice(0, 800)" not in main
    assert "function availabilityPager(" in main
    assert 'aria-label="共同空闲分页"' in main
    assert "data-availability-page" in main
    assert "共 ${total} 个时段，当前第 ${page + 1} / ${pageCount} 页" in main
    assert "const pageSize = 800;" in main


def test_review_view_removes_root_click_listener_on_destroy() -> None:
    review = _source("src/reviewView.ts")

    assert 'root.addEventListener("click", handleRootClick);' in review
    assert 'root.removeEventListener("click", handleRootClick);' in review
    assert 'root.addEventListener("click", (event)' not in review


def test_retry_resets_stale_result_and_review_before_starting() -> None:
    main = _source("src/main.ts")
    retry = _function_body(main, "async function retryFailed()", "async function exportExcel()")

    assert "state.result = null;" in retry
    assert "state.review = null;" in retry
    assert 'state.stage = "queued";' in retry
    assert "resetRichViews();" in main


def test_member_schedule_choices_clear_stale_job_reference() -> None:
    main = _source("src/main.ts")
    choose = _function_body(main, "async function chooseFiles()", "async function parseSchedules()")

    assert 'state.jobId = "";' in choose
    assert "availabilityPage = 0;" in choose


def test_review_and_settings_failures_surface_to_the_user() -> None:
    main = _source("src/main.ts")
    review = _source("src/reviewView.ts")

    assert "修正没有保存" in main
    assert "学期设置保存失败" in main
    assert "saveSettings().catch(() => undefined)" in main
    assert "// 失败原因由上层状态栏显示，这里只避免未处理的 Promise rejection。" in review


def test_correction_refreshes_stage_with_result_export_state() -> None:
    main = _source("src/main.ts")
    refresh = _function_body(main, "async function refreshJobOutput()", "const render = createFrameScheduler")

    assert "state.stage = state.result.can_export ? \"completed\" : \"review\";" in refresh


def test_calendar_registers_chinese_locale_explicitly() -> None:
    calendar = _source("src/calendarView.ts")

    assert 'from "@fullcalendar/core/locales/zh-cn"' in calendar
    assert "locales: [zhCnLocale]" in calendar


def test_failure_state_does_not_render_the_brand_character() -> None:
    main = _source("src/main.ts")

    assert 'renderWelcome(state.stage !== "failed")' in main
    assert '${showAgu ? renderAguMotion("welcome") : ""}' in main
