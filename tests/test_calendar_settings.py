import os

from app.offline_resources import configure_offline_environment
from app.services.calendar_settings import load_calendar_settings, save_calendar_settings


def test_calendar_settings_persist_and_apply_to_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))
    paths = configure_offline_environment()

    saved = save_calendar_settings(
        paths,
        {
            "semester_start_date": "2026-09-07",
            "teaching_weeks": 20,
        },
    )
    loaded = load_calendar_settings(paths)

    assert saved == {"semester_start_date": "2026-09-07", "teaching_weeks": 20}
    assert loaded == saved
    assert os.environ["KONGGU_SEMESTER_START_DATE"] == "2026-09-07"
    assert os.environ["KONGGU_TEACHING_WEEKS"] == "20"


def test_calendar_settings_reject_invalid_values(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))
    paths = configure_offline_environment()

    try:
        save_calendar_settings(paths, {"semester_start_date": "bad", "teaching_weeks": 18})
    except ValueError as exc:
        assert "YYYY-MM-DD" in str(exc)
    else:
        raise AssertionError("Expected invalid date to fail.")
