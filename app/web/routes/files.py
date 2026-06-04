"""File upload endpoints for the Konggu local Web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.bootstrap import load_schedule_core
from app.services.pdf_source_service import add_pdf_sources
from app.web.schemas import normalize_source_type, serialize_uploaded_file
from app.web.task_manager import TASK_NOT_FOUND_MESSAGE, UPLOAD_ROOT, UploadedFileRecord, task_manager
from core.legacy_adapter import infer_member_name_from_filename


router = APIRouter(prefix="/api/files", tags=["files"])
schedule_app = load_schedule_core()


def _unique_upload_path(upload_dir: Path, filename: str) -> Path:
    target = upload_dir / Path(filename).name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 2
    while True:
        candidate = upload_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _infer_source_type(filename: str, path: str) -> str:
    try:
        inferred = schedule_app.infer_pdf_kind(filename, path)
    except Exception:
        inferred = ""
    return normalize_source_type(inferred, filename=filename, path=path)


def _file_warning(*, source_type: str, member_name: str) -> str:
    warnings = []
    if source_type == "unknown":
        warnings.append("无法判断课表类型，请检查文件名是否包含“中方”或“英方”。")
    if not member_name:
        warnings.append("无法推断成员名，请检查文件命名。")
    return "；".join(warnings)


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...), task_id: str = Form("")) -> dict:
    """Upload one or more PDF schedule files into a new or existing local task."""

    if not files:
        raise HTTPException(status_code=400, detail="请先上传课表 PDF。")

    invalid = [file.filename or "" for file in files if Path(file.filename or "").suffix.lower() != ".pdf"]
    if invalid:
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件。")

    task_id = task_id.strip()
    if task_id:
        try:
            task = task_manager.require(task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None
        if task.status == "RUNNING":
            raise HTTPException(status_code=409, detail="任务正在处理中，不能追加上传。")
        if task.status in {"COMPLETED", "EXPORTED"}:
            raise HTTPException(status_code=409, detail="当前任务已生成，请清空任务后再上传新 PDF。")
    else:
        task = task_manager.create_task()

    upload_dir = UPLOAD_ROOT / task.task_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    sizes_by_path: dict[str, int] = {}

    try:
        for upload in files:
            filename = Path(upload.filename or "schedule.pdf").name
            target = _unique_upload_path(upload_dir, filename)
            data = await upload.read()
            target.write_bytes(data)
            saved_paths.append(str(target))
            sizes_by_path[str(target.resolve())] = len(data)

        add_result = add_pdf_sources(
            paths=saved_paths,
            explicit_kind=None,
            existing_sources=list(task.pdf_sources),
            schedule_core=schedule_app,
        )
    except Exception as exc:
        task_manager.set_failed(task.task_id, f"文件上传失败：{exc}")
        raise HTTPException(status_code=500, detail="文件上传失败，请重新选择 PDF 后再试。") from exc

    records = []
    for source in add_result.added:
        source_type = _infer_source_type(source.file_name, source.source_path)
        member_name = infer_member_name_from_filename(source.file_name) or ""
        warning = _file_warning(source_type=source_type, member_name=member_name)
        records.append(
            UploadedFileRecord(
                filename=source.file_name,
                path=source.source_path,
                size=sizes_by_path.get(str(Path(source.source_path).resolve()), 0),
                source_type=source_type,
                member_name=member_name,
                status="uploaded",
                note=warning or "等待解析",
                warning=warning,
            )
        )

    task_manager.add_files(task.task_id, records, add_result.added)
    task_manager.set_status(task.task_id, "UPLOADED")
    task_manager.set_progress(
        task.task_id,
        0.12,
        stage="文件已上传，可继续追加或生成",
        current_file=records[0].filename if records else "",
    )
    task_manager.add_log(task.task_id, "INFO", f"已上传 {len(records)} 个 PDF 文件，当前共 {len(task.uploaded_files)} 个。")

    task = task_manager.require(task.task_id)
    for record in records:
        if record.warning:
            task.warnings.append(f"{record.filename}: {record.warning}")
            task_manager.add_log(task.task_id, "WARNING", f"{record.filename}: {record.warning}")
    for skipped in add_result.skipped:
        task.warnings.append(f"已跳过：{skipped}")
        task_manager.add_log(task.task_id, "WARNING", f"已跳过：{skipped}")
    for pdf_path, exc in add_result.errors:
        message = f"{pdf_path.name}: {exc}"
        task.errors.append(message)
        task_manager.add_log(task.task_id, "ERROR", message)

    if not records:
        task_manager.set_failed(task.task_id, "PDF 文件读取失败，请检查文件是否可访问。")
        raise HTTPException(status_code=400, detail="PDF 文件读取失败，请检查文件是否可访问。")

    return {
        "ok": True,
        "task_id": task.task_id,
        "files": [serialize_uploaded_file(file) for file in task.uploaded_files],
        "warnings": task.warnings,
        "errors": task.errors,
    }


@router.post("/clear")
async def clear_files(payload: dict) -> dict:
    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id。")
    if not task_manager.clear_task(task_id):
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE)
    return {"ok": True}
