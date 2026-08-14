from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_agu_motion_has_distinct_positive_states() -> None:
    motion = _source("src/aguMotion.ts")
    main = _source("src/main.ts")

    assert '"welcome" | "working" | "complete"' in motion
    assert 'renderAguMotion("welcome")' in main
    assert 'renderAguMotion("working", true)' in main
    assert 'renderAguMotion("complete", true)' in main
    assert 'result?.can_export && ["completed", "exported"].includes(state.stage)' in main


def test_agu_motion_uses_staged_actions_instead_of_one_float_loop() -> None:
    styles = _source("src/styles.css")

    for animation in (
        "agu-welcome-rest",
        "agu-welcome-action",
        "agu-working-step",
        "agu-complete-celebrate",
    ):
        assert f"@keyframes {animation}" in styles
    assert "agu-float" not in styles
    assert ".aguMotion__ground" in styles
    assert ".aguMotion__coreGlow" in styles


def test_agu_motion_has_a_static_reduced_motion_fallback() -> None:
    styles = _source("src/styles.css")

    reduced_motion = styles.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "animation: none !important" in reduced_motion
    assert ".aguMotion--welcome .aguMotion__pose--action { display: none; }" in reduced_motion
    assert ".aguMotion--working .aguMotion__pose--action { display: block; opacity: 1; }" in reduced_motion
    assert ".aguMotion--complete .aguMotion__pose--action { display: block; opacity: 1; }" in reduced_motion
