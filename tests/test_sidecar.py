import io
import json
import types

import app.sidecar as sidecar
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
    assert payload["storage"] == "本机应用数据目录（路径已隐藏）"
    assert payload["ocr"]["ready"] is False


def test_sidecar_rejects_unknown_command(tmp_path, monkeypatch):
    monkeypatch.setenv("KONGGU_APP_DATA_ROOT", str(tmp_path / "appdata"))

    try:
        dispatch({"command": "unknown.command"})
    except ValueError as exc:
        assert "Unsupported sidecar command" in str(exc)
    else:
        raise AssertionError("Expected unknown command to fail.")


def test_sidecar_one_shot_error_does_not_return_traceback(monkeypatch):
    monkeypatch.setattr(sidecar, "dispatch", lambda request: (_ for _ in ()).throw(ValueError("C:/Users/member/private.pdf failed")))
    monkeypatch.setattr(sidecar.sys, "stdin", io.StringIO('{"command":"unknown.command"}'))
    output = io.StringIO()
    monkeypatch.setattr(sidecar.sys, "stdout", output)

    assert sidecar.main([]) == 1
    payload = json.loads(output.getvalue())
    assert "traceback" not in payload
    assert "C:/Users/member" not in payload["error"]


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


def test_sidecar_serve_emits_utf8_when_windows_stdio_uses_ansi_encoding(monkeypatch):
    raw_stdout = io.BytesIO()
    stdout = io.TextIOWrapper(raw_stdout, encoding="cp936")
    monkeypatch.setattr(sidecar.sys, "stdout", stdout)
    monkeypatch.setattr(sidecar.sys, "stdin", io.StringIO('{"id":"request-1","command":"resources.repair"}\n'))
    monkeypatch.setattr(sidecar, "configure_offline_environment", lambda: object())
    monkeypatch.setattr(sidecar, "load_calendar_settings", lambda paths: {})
    monkeypatch.setattr(sidecar, "apply_calendar_settings", lambda settings: None)
    monkeypatch.setattr(sidecar, "load_schedule_core", lambda: object())
    monkeypatch.setattr(
        sidecar,
        "StateStore",
        types.SimpleNamespace(from_resource_paths=lambda paths: object()),
    )
    monkeypatch.setattr(sidecar, "JobCoordinator", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        sidecar,
        "dispatch",
        lambda request, coordinator: {"ok": False, "error": "OCR 配置异常：模型启动失败"},
    )

    assert sidecar.serve() == 0
    stdout.flush()

    decoded = raw_stdout.getvalue().decode("utf-8")
    response = json.loads(decoded)
    assert response["ok"] is False
    assert response["error"] == "OCR 配置异常：模型启动失败"
