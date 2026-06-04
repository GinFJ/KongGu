from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pandas as pd

from app.web.main import app
from app.web.task_manager import EXPORT_ROOT, TASK_NOT_FOUND_MESSAGE, UPLOAD_ROOT, task_manager
from core.models import AvailabilitySlot, CourseBlock, FileProcessRecord, MemberSchedule, PdfSource, ProcessResult, ProcessSummary


client = TestClient(app)


def teardown_function():
    for task_id in list(task_manager._tasks):
        task_manager.clear_task(task_id)


def _upload_pdf(filename: str = "测试-中方课表.pdf") -> dict:
    response = client.post(
        "/api/files/upload",
        files=[("files", (filename, b"%PDF-1.4\n%%EOF", "application/pdf"))],
    )
    assert response.status_code == 200
    return response.json()


def _completed_task_with_warning() -> str:
    data = _upload_pdf("张三-中方课表.pdf")
    task_id = data["task_id"]
    source = PdfSource("张三-中方课表.pdf", "涓柟", "D:/fake.pdf")
    block = CourseBlock(name="张三", source_type="涓柟", week=1, weekday="周一", periods=[1])
    member = MemberSchedule(name="张三", has_chinese=True, has_english=False, blocks=[block])
    record = FileProcessRecord(source=source, status="宸茶瘑鍒?", member_name="张三", block_count=1)
    slot = AvailabilitySlot(week=1, date="", weekday="周一", period=1, time_range="", free_members=["李四"], busy_members=["张三"])
    result = ProcessResult(
        blocks=[block],
        members=[member],
        file_records=[record],
        slots=[slot],
        summary=ProcessSummary(member_count=1, source_file_count=1, slot_count=1),
    )
    generation = SimpleNamespace(
        occupancy={(1, "周一", 1): {"张三"}},
        students=["张三", "李四"],
        weeks=[1],
        calendar_df=pd.DataFrame(),
        blocks_df=pd.DataFrame(),
        all_slot_df=pd.DataFrame(),
    )
    task_manager.update(task_id, warnings=["张三-中方课表.pdf: 缺少英方课表"])
    task_manager.set_result(
        task_id,
        result=result,
        generation_result=generation,
        preview_df=pd.DataFrame(),
        summary={
            "pdf_count": 1,
            "member_count": 1,
            "complete_member_count": 0,
            "warning_count": 1,
            "course_block_count": 1,
            "availability_slot_count": 1,
            "acceptance": {
                "uploaded_pdf_count": 1,
                "successful_file_count": 1,
                "failed_file_count": 0,
                "member_count": 1,
                "complete_member_count": 0,
                "missing_chinese_count": 0,
                "missing_english_count": 1,
                "warning_count": 1,
            },
        },
    )
    return task_id


def test_upload_rejects_non_pdf():
    response = client.post(
        "/api/files/upload",
        files=[("files", ("not-a-pdf.txt", b"hello", "text/plain"))],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅支持 PDF 文件。"


def test_upload_pdf_success_returns_file_metadata():
    data = _upload_pdf()

    assert data["ok"] is True
    assert data["task_id"]
    assert data["files"][0]["filename"] == "测试-中方课表.pdf"
    assert data["files"][0]["size"] > 0
    assert data["files"][0]["status"] == "uploaded"
    assert data["files"][0]["source_type"] == "chinese"
    assert "member_name" in data["files"][0]


def test_upload_can_append_pdfs_to_existing_task():
    first = _upload_pdf("刘金富-中方课表.pdf")

    response = client.post(
        "/api/files/upload",
        data={"task_id": first["task_id"]},
        files=[("files", ("刘金富-英方课表.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))],
    )

    assert response.status_code == 200
    second = response.json()
    assert second["task_id"] == first["task_id"]
    assert len(second["files"]) == 2
    assert [file["filename"] for file in second["files"]] == ["刘金富-中方课表.pdf", "刘金富-英方课表.pdf"]
    task = task_manager.require(first["task_id"])
    assert len(task.uploaded_files) == 2
    assert len(task.pdf_sources) == 2


def test_unknown_source_type_file_is_visible_with_warning():
    data = _upload_pdf("mystery.pdf")
    file = data["files"][0]

    assert file["source_type"] == "unknown"
    assert "无法判断课表类型" in file["warning"]
    assert "无法推断成员名" in file["warning"]
    assert data["warnings"]


def test_missing_task_id_returns_clear_error():
    response = client.get("/api/process/status/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == TASK_NOT_FOUND_MESSAGE


def test_export_without_result_is_rejected():
    data = _upload_pdf()

    response = client.post("/api/export", json={"task_id": data["task_id"]})

    assert response.status_code == 400
    assert response.json()["detail"] == "请先生成空课表。"


def test_status_returns_valid_json_for_uploaded_task():
    data = _upload_pdf()

    response = client.get(f"/api/process/status/{data['task_id']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "UPLOADED"
    assert payload["summary"]["pdf_count"] == 1
    assert payload["acceptance_summary"]["uploaded_pdf_count"] == 1
    assert isinstance(payload["logs"], list)
    assert {"time", "level", "message"}.issubset(payload["logs"][0])


def test_results_before_completion_returns_empty_arrays():
    data = _upload_pdf()

    response = client.get(f"/api/results/{data['task_id']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "UPLOADED"
    assert payload["availability"] == []
    assert payload["members"] == []
    assert payload["details"] == []
    assert payload["warnings"] == []
    assert payload["errors"] == []


def test_members_missing_chinese_returns_risk_prompt():
    task_id = _completed_task_with_warning()
    task = task_manager.require(task_id)
    member = MemberSchedule(name="李四", has_chinese=False, has_english=True, blocks=[task.result.blocks[0]])
    task.result.members = [member]

    response = client.get(f"/api/results/{task_id}")

    payload = response.json()
    assert payload["members"][0]["status"] == "需检查"
    assert payload["members"][0]["risk"] == "缺少中方课表"


def test_members_missing_english_returns_risk_prompt():
    task_id = _completed_task_with_warning()

    response = client.get(f"/api/results/{task_id}")

    payload = response.json()
    assert payload["members"][0]["status"] == "需检查"
    assert payload["members"][0]["risk"] == "缺少英方课表"


def test_results_details_tolerate_missing_optional_fields():
    task_id = _completed_task_with_warning()
    task = task_manager.require(task_id)
    task.result.file_records[0].text_source = ""
    task.result.file_records[0].result_message = ""

    response = client.get(f"/api/results/{task_id}")

    payload = response.json()
    assert payload["details"][0]["parser"] == "暂未提供"
    assert "error" in payload["details"][0]


def test_partial_warning_result_remains_completed():
    task_id = _completed_task_with_warning()

    response = client.get(f"/api/process/status/{task_id}")

    payload = response.json()
    assert payload["status"] == "COMPLETED"
    assert payload["summary"]["warning_count"] == 1
    assert payload["acceptance_summary"]["missing_english_count"] == 1


def test_export_with_warning_and_result_is_allowed(monkeypatch):
    task_id = _completed_task_with_warning()

    from app.web.routes import export as export_route

    monkeypatch.setattr(export_route, "build_export_excel_bytes", lambda **kwargs: b"xlsx")
    response = client.post("/api/export", json={"task_id": task_id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["download_url"] == f"/api/export/download/{task_id}"
    task = task_manager.require(task_id)
    assert task.status == "EXPORTED"
    assert any(log.level == "EXPORT" and "Excel 导出成功" in log.message for log in task.logs)


def test_clear_task_removes_task_and_runtime_dirs():
    data = _upload_pdf()
    task_id = data["task_id"]
    export_dir = EXPORT_ROOT / task_id
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "dummy.xlsx").write_bytes(b"xlsx")

    clear_response = client.post("/api/files/clear", json={"task_id": task_id})
    status_response = client.get(f"/api/process/status/{task_id}")

    assert clear_response.status_code == 200
    assert clear_response.json() == {"ok": True}
    assert status_response.status_code == 404
    assert status_response.json()["detail"] == TASK_NOT_FOUND_MESSAGE
    assert not (UPLOAD_ROOT / task_id).exists()
    assert not export_dir.exists()
