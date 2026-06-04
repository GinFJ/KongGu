"""Excel export endpoints for the Konggu local Web UI."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.bootstrap import load_schedule_core
from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes
from app.web.task_manager import EXPORT_ROOT, TASK_NOT_FOUND_MESSAGE, task_manager


router = APIRouter(prefix="/api/export", tags=["export"])
schedule_app = load_schedule_core()
LOGGER = logging.getLogger("konggu.web")


def _safe_export_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"Konggu_availability_{stamp}.xlsx"


@router.post("")
async def export_excel(payload: dict) -> dict:
    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id。")
    try:
        task = task_manager.require(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None

    if task.result is None or task.generation_result is None:
        raise HTTPException(status_code=400, detail="请先生成空课表。")

    generation = task.generation_result
    try:
        data = build_export_excel_bytes(
            schedule_core=schedule_app,
            request=ExcelExportRequest(
                occupancy=generation.occupancy,
                students=generation.students,
                weeks=generation.weeks,
                calendar_df=generation.calendar_df,
                timetable_df=schedule_app.default_timetable(),
                threshold=0,
                blocks_df=generation.blocks_df,
                all_slot_df=generation.all_slot_df,
            ),
        )
        export_dir = EXPORT_ROOT / task_id
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / _safe_export_name()
        export_path.write_bytes(data)
    except PermissionError:
        message = "Excel 文件可能正在被 WPS 或 Excel 打开，请关闭后重试。"
        task_manager.add_log(task_id, "ERROR", f"Excel 导出失败：{message}")
        raise HTTPException(status_code=423, detail=message) from None
    except Exception as exc:
        LOGGER.exception("Excel export failed for task_id=%s", task_id)
        message = f"Excel 导出失败：{exc}"
        task_manager.add_log(task_id, "ERROR", message)
        raise HTTPException(status_code=500, detail=message) from exc

    task_manager.update(task_id, export_path=str(export_path))
    task_manager.set_status(task_id, "EXPORTED")
    if task.warnings:
        task_manager.add_log(task_id, "WARNING", "当前结果存在警告，建议检查后再使用。")
    task_manager.add_log(task_id, "EXPORT", "Excel 导出成功。")
    return {"ok": True, "download_url": f"/api/export/download/{task_id}"}


@router.get("/download/{task_id}")
async def download_excel(task_id: str) -> FileResponse:
    try:
        task = task_manager.require(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND_MESSAGE) from None
    if not task.export_path:
        raise HTTPException(status_code=404, detail="尚未导出 Excel 文件。")

    path = Path(task.export_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Excel 文件不存在，请重新导出。")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )

