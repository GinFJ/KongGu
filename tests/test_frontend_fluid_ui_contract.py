from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_app_shell_is_mounted_once_and_updates_are_frame_coalesced() -> None:
    main = _source("src/main.ts")
    motion = _source("src/uiMotion.ts")

    assert main.count("appRoot.innerHTML =") == 1
    assert "function mountShell()" in main
    assert "function updateShell()" in main
    assert "const render = createFrameScheduler" in main
    assert "window.requestAnimationFrame" in motion
    assert "if (frame !== null) return" in motion


def test_rich_views_have_stable_lifecycle_and_preserve_view_state() -> None:
    main = _source("src/main.ts")
    review = _source("src/reviewView.ts")

    assert 'data-panel="${tab}"' in main
    assert "calendar?.updateSize()" in main
    assert "reviewController.update(state.review)" in main
    assert "export type ReviewController" in review
    assert "getViewState" in review
    assert "scrollLeft" in review
    assert "scrollTop" in review
    assert "renderTask?.cancel()" in review


def test_fluid_ui_has_motion_and_accessibility_fallbacks() -> None:
    styles = _source("src/styles.css")

    for token in (
        "--motion-press: 90ms",
        "--motion-hover: 140ms",
        "--motion-content: 220ms",
        "--motion-panel: 300ms",
        "prefers-reduced-motion: reduce",
        "prefers-reduced-transparency: reduce",
        "prefers-contrast: more",
    ):
        assert token in styles
    assert ".tabIndicator" in styles
    assert ".sourceChip.is-removing" in styles
    assert "grid-template-columns: repeat(auto-fit, minmax(min(250px, 100%), 1fr));" in styles
    assert "overscroll-behavior: contain;" in styles
    assert "transform: scaleX(var(--progress-scale, 0))" in styles


def test_ui_motion_uses_no_new_runtime_dependency() -> None:
    package = _source("package.json")
    motion = _source("src/uiMotion.ts")

    assert '"motion"' not in package
    assert '"framer-motion"' not in package
    assert "matchMedia" in motion
    assert "commitAfterExit" in motion


def test_supported_minimum_height_uses_compact_chrome_without_outer_scroll() -> None:
    styles = _source("src/styles.css")

    assert "@media (max-height: 760px)" in styles
    assert ".shell { min-height: 0;" in styles
    assert ".workbench {\n    grid-template-rows: auto auto auto minmax(0, 1fr);" in styles
    assert ".activityLog { display: none; }" in styles
    assert "html, body, #app { width: 100%; height: 100%; margin: 0; overflow: hidden; }" in styles


def test_cancel_fallback_survives_late_progress_events() -> None:
    main = _source("src/main.ts")

    assert "cancelRequestedJobId" in main
    assert "cancelRequestedJobId === jobId" in main
