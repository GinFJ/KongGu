"""Current local batch and explicitly remembered original-file locations."""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path

from core.legacy_adapter import hash_file
from core.pdf_inspection import MAX_PDF_BYTES
from .state_store import RETENTION_DAYS, StateStore, utc_now


def _selection(files, remember):
    entries = [{"name": Path(path).name, "path": path if remember and Path(path).is_absolute() else ""}
               for path in files]
    return json.dumps(entries, ensure_ascii=False)


def write_workspace(db, files, remember, job_id=""):
    selection = _selection(files, remember)
    now = utc_now()
    db.execute("INSERT OR REPLACE INTO workspace(id,job_id,selection,remember,updated_at) VALUES(1,?,?,?,?)",
               (job_id, selection, int(remember), now))
    if job_id:
        _retain_batch(db, job_id, selection, remember, now)


def _retain_batch(db, job_id, selection, remember, stamp):
    db.execute("INSERT OR REPLACE INTO local_batch_history(job_id,keep_result,selection,remember,paths_updated_at)"
               " VALUES(?,1,?,?,?)", (job_id, selection if remember else "[]", int(remember), stamp))


_COMPLETED = "j.status='completed' AND j.result_json IS NOT NULL"


def count_history(store):
    with store.connect() as db:
        return db.execute(f"SELECT COUNT(*) FROM jobs j WHERE {_COMPLETED}").fetchone()[0]


def list_history(store):
    """List small batch headers only; opening the picker must not decode every snapshot."""
    with store.connect() as db:
        rows = db.execute(f"""
            SELECT j.id AS job_id,j.updated_at,json_extract(j.request_json,'$.start_date') AS start_date,
                   json_extract(j.request_json,'$.end_date') AS end_date,
                   (SELECT COUNT(*) FROM job_files f WHERE f.job_id=j.id) AS file_count,
                   (SELECT json_group_array(file_name) FROM
                       (SELECT file_name FROM job_files f WHERE f.job_id=j.id ORDER BY rowid LIMIT 3)) AS names
            FROM jobs j WHERE {_COMPLETED} ORDER BY j.updated_at DESC,j.rowid DESC
        """).fetchall()
        draft = db.execute("""SELECT 1 FROM workspace w LEFT JOIN jobs j ON j.id=w.job_id
            LEFT JOIN workspace_dates d ON d.workspace_id=w.id
            WHERE (j.status IS NULL OR j.status<>'completed')
              AND (json_array_length(w.selection)>0 OR d.start_date IS NOT NULL)""").fetchone()
    return {"ok": True, "batches": [
        {"job_id": row["job_id"], "updated_at": row["updated_at"], "file_count": row["file_count"],
         "file_names": json.loads(row["names"]), "start_date": row["start_date"] or "",
         "end_date": row["end_date"] or ""} for row in rows
    ], "draft": bool(draft)}


def _matching_calendar(db, job_id, request, digest):
    """Only restore settings that reproduce the recorded parser signature exactly."""
    from core.signature import build_parser_signature
    from .calendar_settings import normalize_calendar_settings

    signature = build_parser_signature()
    if digest == signature.digest:
        return {"semester_start_date": signature.calendar_start, "teaching_weeks": int(signature.teaching_weeks)}
    saved = request.get("workspace_calendar")
    if saved:
        try:
            candidates = [normalize_calendar_settings(saved)]
        except (AttributeError, ValueError):
            return None
    else:
        # Older results predate workspace_calendar. A dated week in the snapshot
        # gives the semester start, even for calendars cropped to a date range.
        row = db.execute("SELECT json_extract(result_json,'$.generation.calendar_rows[0]') FROM jobs WHERE id=?",
                         (job_id,)).fetchone()[0]
        if not row:
            return None
        try:
            first = json.loads(row)
            day = date.fromisoformat(first["date"][:10])
            start = day - timedelta(days=(int(first["week"]) - 1) * 7 + day.weekday())
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        candidates = [{"semester_start_date": start.isoformat(), "teaching_weeks": weeks}
                      for weeks in range(1, 31)]
    for calendar in candidates:
        candidate = replace(signature, calendar_start=calendar["semester_start_date"],
                            teaching_weeks=str(calendar["teaching_weeks"]))
        if candidate.digest == digest:
            return calendar
    return None


def open_history(store, job_id, resource_paths=None):
    from .calendar_settings import load_calendar_settings, save_calendar_settings

    restored_calendar = None
    previous_calendar = None
    cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"""SELECT j.request_json,j.parser_signature,h.selection,h.remember,h.paths_updated_at
                FROM jobs j LEFT JOIN local_batch_history h ON h.job_id=j.id
                WHERE j.id=? AND {_COMPLETED}""", (job_id,)).fetchone()
            if not row:
                raise ValueError("找不到这份本机历史课表，请刷新列表。")
            recent = bool(row["paths_updated_at"] and row["paths_updated_at"] >= cutoff)
            entries = json.loads(row["selection"]) if recent and row["selection"] else []
            if not entries:
                names = db.execute("SELECT file_name FROM job_files WHERE job_id=? ORDER BY rowid", (job_id,))
                entries = [{"name": record[0], "path": ""} for record in names]
            selection = json.dumps(entries, ensure_ascii=False)
            remember = bool(row["remember"])
            # Simply viewing a batch never extends the consent to retain its original locations.
            stamp = row["paths_updated_at"] if recent else utc_now()
            db.execute("INSERT OR REPLACE INTO workspace(id,job_id,selection,remember,updated_at) VALUES(1,?,?,?,?)",
                       (job_id, selection, int(remember), stamp))
            _retain_batch(db, job_id, selection, remember, stamp)
            if resource_paths is not None:
                restored_calendar = _matching_calendar(db, job_id, json.loads(row["request_json"]), row["parser_signature"])
                if restored_calendar:
                    current_calendar = load_calendar_settings(resource_paths)
                    if restored_calendar != current_calendar:
                        save_calendar_settings(resource_paths, restored_calendar)
                        previous_calendar = current_calendar
    except Exception:
        if previous_calendar is not None:
            save_calendar_settings(resource_paths, previous_calendar)
        raise
    workspace = load_workspace(store)
    if workspace["job_id"] != job_id:
        raise ValueError("当前课表批次已变化，请重新打开。")
    if restored_calendar:
        workspace["calendar"] = restored_calendar
    return workspace


def save_draft(store, files, remember, start_date=None, end_date=None):
    _validate_selection(files)
    if start_date is not None or end_date is not None:
        from .date_review import date_bounds
        date_bounds({"start_date": start_date, "end_date": end_date})
    with store.connect() as db:
        write_workspace(db, files, remember)
        if start_date:
            db.execute("INSERT INTO workspace_dates VALUES(1,?,?)", (start_date, end_date))
    return {"ok": True}


def _validate_selection(files):
    from .desktop_workflow import MAX_INPUT_FILES
    if (not isinstance(files, list) or len(files) > MAX_INPUT_FILES or
            any(not isinstance(p, str) or not p or Path(p).name in {"", ".", ".."} for p in files)):
        raise ValueError("课表列表无效，请重新选择文件。")


def load_workspace(store: StateStore):
    with store.connect() as db:
        dates = db.execute("SELECT start_date,end_date FROM workspace_dates WHERE workspace_id=1").fetchone()
        row = db.execute("SELECT * FROM workspace WHERE id=1").fetchone()
        if row:
            job_id, entries, remember = row["job_id"], json.loads(row["selection"]), bool(row["remember"])
        else:
            # Old releases already stored results but had no workspace pointer.
            last = db.execute("SELECT id FROM jobs WHERE status='completed' AND result_json IS NOT NULL "
                              "ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
            job_id, entries, remember = last["id"] if last else "", [], False
    job = store.get_job(job_id, include_result=False) if job_id else None
    if job:
        records = job["files"]
        if [e["name"] for e in entries] != [f["file_name"] for f in records]:
            entries = [{"name": f["file_name"], "path": ""} for f in records]
        live = store.source_paths_for_job(job_id)
        for i, (entry, record) in enumerate(zip(entries, records)):
            hint = (live[i] if i < len(live) else "") or entry["path"]
            entry["path"] = _matching_path(hint, entry["name"], record.get("source_hash"),
                                            required=job["status"] == "completed")
        store.bind_session_paths(job_id, [e["path"] for e in entries])
    else:
        job_id = ""
        for entry in entries:
            entry["path"] = _matching_path(entry["path"], entry["name"], None)
    bounds = (job.get("request") or {}) if job else dict(dates or {})
    return {"ok": True, "job_id": job_id, "status": job["status"] if job else "",
            **{k: bounds[k] for k in ("start_date", "end_date") if bounds.get(k)},
            "files": entries, "remember": remember if (entries or row) else True}


def _matching_path(hint, name, digest, required=False):
    if not hint or (required and not digest):
        return ""
    source = Path(hint)
    try:
        if (not source.is_absolute() or source.name != name or source.suffix.lower() != ".pdf"
                or not source.is_file() or source.stat().st_size > MAX_PDF_BYTES):
            return ""
        return str(source) if not digest or hash_file(source) == digest else ""
    except OSError:
        return ""


def attach_originals(store, job_id, paths, remember):
    _validate_selection(paths)
    workspace = load_workspace(store)
    if not paths or workspace["job_id"] != job_id:
        raise ValueError("当前课表批次已变化，请重新打开。")
    files = workspace["files"]
    job = store.get_job(job_id, include_result=False) if job_id else None
    names = [f["name"] for f in files]
    used = set()
    for path in paths:
        name = Path(path).name
        if names.count(name) != 1 or name in used:
            raise ValueError("请选择本批文件列表中同名且唯一的原件。")
        i = names.index(name)
        digest = job["files"][i].get("source_hash") if job else None
        if not _matching_path(path, name, digest, required=bool(job and job["status"] == "completed")):
            raise ValueError(f"「{name}」与本批原件内容不一致，或已无法读取。")
        files[i]["path"] = path
        used.add(name)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT job_id,selection FROM workspace WHERE id=1").fetchone()
        if current:
            saved_files = json.loads(current["selection"])
            if current["job_id"] != job_id or [item["name"] for item in saved_files] != names:
                raise ValueError("当前课表批次已变化，请重新打开。")
            for item, prior in zip(files, saved_files):
                if item["name"] not in used and prior["path"]:
                    item["path"] = prior["path"]
            selection = _selection([f["path"] or f["name"] for f in files], remember)
            stamp = utc_now()
            db.execute("UPDATE workspace SET selection=?,remember=?,updated_at=? WHERE id=1",
                       (selection, int(remember), stamp))
            if job_id:
                _retain_batch(db, job_id, selection, remember, stamp)
        elif job_id:
            # A pre-workspace job can be opened by the legacy fallback.
            write_workspace(db, [f["path"] or f["name"] for f in files], remember, job_id)
        else:
            raise ValueError("当前课表批次已变化，请重新打开。")
    if job:
        store.bind_session_paths(job_id, [f["path"] for f in files])
    return {"ok": True, "files": files}


def read_bound_pdf(store, job_id, source_hash, file_name):
    # After a WebView restarts its dialog permission has expired. Only serve a
    # file previously linked to this job and matching the exact recorded bytes.
    job = store.get_job(job_id, include_result=False, include_sensitive_paths=True)
    candidates = [f["source_path"] for f in (job or {}).get("files", [])
                  if source_hash and f.get("source_hash") == source_hash and
                  f.get("file_name") == file_name and f.get("source_path")]
    if len(candidates) != 1:
        raise ValueError("请先关联这份原件。")
    path = Path(candidates[0])
    if path.suffix.lower() != ".pdf" or path.stat().st_size > MAX_PDF_BYTES:
        raise ValueError("本批原件无法读取，请重新关联。")
    with path.open("rb") as stream:
        data = stream.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES or hashlib.sha256(data).hexdigest() != source_hash:
        raise ValueError("原件内容已变化，请重新关联同一份原件。")
    return {"ok": True, "data": base64.b64encode(data).decode("ascii")}
