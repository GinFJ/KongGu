from app.sidecar import dispatch
import fitz


def test_sidecar_resource_status_returns_json_safe_payload(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "resources").mkdir(parents=True)
    (bundled / "resources" / "offline_manifest.json").write_text('{"version":1,"entries":[]}', encoding="utf-8")
    monkeypatch.setenv("KONGGU_BUNDLED_RESOURCE_ROOT", str(bundled))
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))

    payload = dispatch({"command": "resources.status"})

    assert payload["ok"] is True
    assert "app_data_root" in payload
    assert payload["ocr"]["ready"] is False


def test_sidecar_rejects_unknown_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))

    try:
        dispatch({"command": "unknown.command"})
    except ValueError as exc:
        assert "Unsupported sidecar command" in str(exc)
    else:
        raise AssertionError("Expected unknown command to fail.")


def test_sidecar_calendar_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))

    saved = dispatch(
        {
            "command": "settings.calendar.save",
            "settings": {"semester_start_date": "2026-09-07", "teaching_weeks": 20},
        }
    )
    loaded = dispatch({"command": "settings.calendar.get"})

    assert saved["ok"] is True
    assert saved["settings"] == {"semester_start_date": "2026-09-07", "teaching_weeks": 20}
    assert loaded["settings"] == saved["settings"]


def test_sidecar_pdf_inspect_returns_local_structure_report(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))
    pdf = tmp_path / "成员甲-中方课表.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Konggu timetable Monday Week 1 course")
    document.save(pdf)
    document.close()

    payload = dispatch({"command": "pdf.inspect", "paths": [str(pdf)]})

    assert payload["ok"] is True
    assert payload["inspections"][0]["engine"] == "firecrawl_pdf_inspector"
    assert payload["inspections"][0]["pdf_type"] == "text_based"
