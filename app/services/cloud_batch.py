"""Portable account batch. Only structured timetable and review fields cross this boundary."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
import math
from pathlib import PureWindowsPath
import re
from uuid import UUID, uuid4

from app.services.calendar_settings import (
    apply_calendar_settings, load_calendar_settings, normalize_calendar_settings, save_calendar_settings,
)
from app.services.desktop_workflow import MAX_INPUT_FILES, WEEKDAYS, _notice_consensus_issue
from app.services.review_operations import OPERATION_FIELD, OPERATION_LABELS
from app.services.review_service import EDITABLE_BLOCK_FIELDS, _refresh_derived
from app.services.state_store import utc_now
from app.services.workflow_persistence import SNAPSHOT_VERSION, restore_workflow, snapshot_workflow
from app.services.workspace_session import write_workspace
from core.models import CorrectionRecord, ParseIssue
from core.pdf_inspection import PdfInspectionResult
from core.quality import quality_state
from core.signature import build_parser_signature


FORMAT = "konggu-batch-v1"
DATED_FORMAT = "konggu-batch-v2"
INVALID = "云端课表格式或来源绑定无效。"
DIGEST = re.compile(r"[0-9a-f]{64}")
BLOCK_FIELDS = """block_id member_key name department role display_suffix source source_type kind week weekday period
    periods date time start_time end_time course source_file source_hash page page_width page_height bbox
    x0 y0 x1 y1 confidence text_source""".split()
REGION_FIELDS = """kind page bbox page_width page_height canonical_label week weekday periods source_hash
    parser_signature ocr_signature action requires_review occupancy_changed dpi""".split()
SENSITIVE_TEXT = re.compile(r"(?:-----BEGIN [^-]*PRIVATE KEY-----|(?:sk-(?:proj-)?|Bearer )"
                            r"[A-Za-z0-9_-]{20,}|data:[\w/+.-]+;base64,|%PDF-\d)", re.I)


# A slash in a subject such as "Systems/Networks" or "C/C++" is ordinary text.
LOCAL_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|(?<![\w/])/(?:[^ /\s]+/)+[^ /\s])")


def _require(ok):
    if not ok:
        raise ValueError(INVALID)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _checksum(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value):
    # Private form fields, logs and OCR documents are never selected. For the user-editable
    # labels that are selected, fail closed on file paths, credential markers and binary data.
    if isinstance(value, str):
        _require(len(value) <= 4000 and not SENSITIVE_TEXT.search(value) and "\x00" not in value)
        _require(not LOCAL_PATH.search(value))
    return value


def _plain(value):
    if isinstance(value, list):
        _require(len(value) <= 1000)
        return [_plain(item) for item in value]
    _require(value is None or isinstance(value, (str, int, float, bool)))
    _require(not isinstance(value, float) or math.isfinite(value))
    return _text(value)


def _fields(value, keys):
    _require(isinstance(value, dict))
    return {key: _plain(value[key]) for key in keys if key in value}


def _filename(name):
    _require(isinstance(name, str) and name == PureWindowsPath(name).name
             and name.lower().endswith(".pdf") and not any(c in name for c in '<>:"/\\|?*')
             and all(ord(c) >= 32 for c in name) and len(name) <= 255)
    return name


def _region(note):
    return _fields(note, REGION_FIELDS)


def _block(row):
    block = _fields(row, BLOCK_FIELDS)
    if "provenance" in row:
        evidence = row["provenance"]
        block["provenance"] = _fields(evidence, (
            "method", "manual_entry", "operation_id", "restored_region", "cell_text_incomplete",
            "obstructed", "date_evidence", "periods", "time_mapping",
        ))
        if evidence.get("identity_completion"):
            block["provenance"]["identity_completion"] = _fields(evidence["identity_completion"], (
                "method", "peer_source_hash", "source_hash", "original_department", "original_role", "department", "role",
            ))
    return block


def _inspection(item):
    fields = set(PdfInspectionResult.__dataclass_fields__) - {"ocr_reasons_by_page", "warning", "error"}
    clean = _fields(item, fields)
    clean["ocr_reasons_by_page"] = [_fields(entry, ("page", "reasons")) for entry in item.get("ocr_reasons_by_page", [])]
    if item.get("error"):
        clean["error"] = "原件结构检查未通过，请在本机核对。"
    if item.get("date_coverage") is not None:
        clean["date_coverage"] = _fields(item["date_coverage"], ("source_hash", "dates", "method"))
    if "nonclass_consensus" in item:
        clean["nonclass_consensus"] = [_region(note) for note in item["nonclass_consensus"]]
    if "english_grid_evidence" in item:
        clean["english_grid_evidence"] = [_fields(note, ("kind", "page", "date", "source_hash", "method"))
                                          for note in item["english_grid_evidence"]]
    return clean


def _correction(record, region_ids):
    fields = set(CorrectionRecord.__dataclass_fields__) - {"original_value", "new_value"}
    clean = _fields(record, fields)
    if clean["field"] in EDITABLE_BLOCK_FIELDS:
        return {**clean, **_fields(record, ("original_value", "new_value"))}
    _require(clean["field"] == OPERATION_FIELD)
    original = record["new_value"]
    op = _fields(original, ("action", "operation_id", "undo_of", "region_id"))
    _require(op["action"] in OPERATION_LABELS)
    binding = original["binding"]
    op["binding"] = {**_fields(binding, ("source_hash", "file_name", "kind")),
                     "identity": _fields(binding["identity"], ("name", "department", "role", "member_key"))}
    op["changes"] = [{key: _block(block) if block is not None else None for key, block in change.items()
                      if key in {"before", "after", "expected_active", "expected_removed"}}
                     for change in original["changes"]]
    if op.get("region_id"):
        op["region_id"] = region_ids.get(op["region_id"], op["region_id"])
        note = original["region_evidence"]
        op["region_evidence"] = {**_region(note), "region_id": op["region_id"], "source_file": _filename(note["source_file"])}
    return {**clean, "original_value": [change["before"] for change in op["changes"]], "new_value": op}


def _date_review(state):
    from app.services.date_review import date_bounds

    result = date_bounds(state)
    _require(result is not None and isinstance(state["confirmed"], dict) and isinstance(state["log"], list))
    result["confirmed"] = {_text(key): _plain(digest) for key, digest in state["confirmed"].items()}
    result["log"] = []
    for event in state["log"]:
        fields = ("operation_id", "action", "member_key", "created_at")
        action = event["action"]
        _require(action in {"slot", "member", "flag"})
        if action == "member":
            fields += ("acknowledge_missing", "missing_dates")
        else:
            fields += ("source_hash", "date", "period", "page")
            fields += ("marked",) if action == "slot" else ("open", "bbox", "page_width", "page_height")
        result["log"].append(_fields(event, fields))
    if "removed_slots" in state:
        saved = state["removed_slots"]
        _require(isinstance(saved, dict))
        result["removed_slots"] = {}
        for key, rows in saved.items():
            owner, digest, day, period = key.rsplit("/", 3)
            _require(isinstance(rows, list) and rows and _text(owner)
                     and (digest == "manual" or DIGEST.fullmatch(digest))
                     and date.fromisoformat(day).isoformat() == day and period in {str(n) for n in range(1, 14)})
            result["removed_slots"][key] = [_block(row) for row in rows]
    return result


def _project(snapshot, files):
    _require(snapshot["version"] == SNAPSHOT_VERSION)
    data = snapshot["generation"]
    inspected = [_inspection(item) for item in data.get("pdf_inspections", [])]
    region_ids = {}
    region_issues = {}
    for item, clean in zip(data.get("pdf_inspections", []), inspected):
        for note, reduced in zip(item.get("nonclass_consensus", []), clean.get("nonclass_consensus", [])):
            if note.get("kind") == "same_page_nonclass_consensus":
                old = _notice_consensus_issue(item["source_file"], item["source_hash"], note)
                new = _notice_consensus_issue(item["source_file"], item["source_hash"], reduced)
                region_ids[old.issue_id] = new.issue_id
                region_issues[old.issue_id] = asdict(new)
    issues = []
    for issue in snapshot["issues"]:
        reduced = region_issues.get(issue["issue_id"])
        if reduced:
            issues.append({**reduced, "confirmed": issue.get("confirmed") is True})
        else:
            issues.append(_fields(issue, ParseIssue.__dataclass_fields__))
    # File errors are represented by source and outcome, never by exception/log text.
    failed_names = {f["file_name"] for f in files if f.get("status") == "failed"}
    sources = [_fields(row, ("file_name", "kind", "content_hash")) for row in data["sources"]]
    profiles = data.get("layout_profiles", {})
    return {
        "version": SNAPSHOT_VERSION, "parser_signature": snapshot["parser_signature"],
        **({"date_review": _date_review(snapshot["date_review"])} if "date_review" in snapshot else {}),
        "quality_state": snapshot["quality_state"], "issues": issues,
        "verified_items": list(snapshot.get("verified_items", [])),
        "corrections": [_correction(row, region_ids) for row in snapshot.get("corrections", [])],
        "generation": {
            "sources": sources, "blocks": [_block(row) for row in data["blocks"]],
            "removed_blocks": [_block(row) for row in data.get("removed_blocks", [])],
            "calendar_rows": [_fields(row, ("date", "week", "weekday")) for row in data["calendar_rows"]],
            "weeks": [_plain(week) for week in data["weeks"]],
            "pdf_inspections": inspected,
            "errors": [name + "：原件未能完整解析。" for name in sorted(failed_names)],
            "layout_profiles": {s["content_hash"]: _plain(profiles[s["content_hash"]])
                                for s in sources if s["content_hash"] in profiles},
        },
    }


def _normalize_manual_pages(snapshot):
    from app.services.date_review import normalize_manual_slot_page
    state = snapshot.get("date_review")
    if state:
        state["log"] = [normalize_manual_slot_page(event) for event in state["log"]]


def _signature(calendar):
    return replace(build_parser_signature(), calendar_start=calendar["semester_start_date"],
                   teaching_weeks=str(calendar["teaching_weeks"])).digest


def export_batch(store, job_id, resource_paths):
    job = store.get_job(job_id)
    if not job or job["status"] != "completed" or not job.get("result"):
        raise ValueError("请先完成本批课表识别，再保存到账号。")
    calendar = load_calendar_settings(resource_paths)
    snapshot = job["result"]
    if _signature(calendar) != snapshot.get("parser_signature"):
        raise ValueError("本批课表与当前学期或识别规则不一致，请先重新生成。")
    files = [_fields(row, ("file_name", "source_hash", "status", "stage")) for row in job["files"]]
    # Jobs from older local APIs may carry digests only in the saved workflow.
    hashes = {s["file_name"]: s["content_hash"] for s in snapshot["generation"]["sources"]}
    for row in files:
        _filename(row["file_name"])
        if not row.get("source_hash"):
            row["source_hash"] = hashes.get(row["file_name"], "")
    document = {"format": DATED_FORMAT if "date_review" in snapshot else FORMAT,
                "calendar": calendar, "batch_id": job["request"].get("cloud_batch_id") or job_id,
                "files": files, "snapshot": _project(snapshot, files)}
    _normalize_manual_pages(document["snapshot"])
    return {**document, "checksum": _checksum(document)}


def _check_date_review(state, data, real_owners):
    from app.services.date_review import review_members

    members = {m["member_key"]: m for m in review_members(state, data)}
    dates = {row["date"]: (row["week"], row["weekday"]) for row in data["calendar_rows"]
             if state["start_date"] <= row["date"] <= state["end_date"]}
    _require(len(dates) == (date.fromisoformat(state["end_date"]) - date.fromisoformat(state["start_date"])).days + 1)
    latest, operations, last_slot, open_flags = {}, {}, {}, {}
    page_counts = {item["source_hash"]: item["page_count"] for item in data["pdf_inspections"]
                   if type(item.get("page_count")) is int and item["page_count"] > 0}
    for event in state["log"]:
        op, owner = event["operation_id"], event["member_key"]
        _require(str(UUID(op)) == op and op not in operations and owner in members)
        _require(datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).tzinfo is not None)
        operations[op] = latest[owner] = event
        if event["action"] == "member":
            missing = event["missing_dates"]
            _require(isinstance(missing, list) and len(set(missing)) == len(missing) and set(missing) <= dates.keys()
                     and type(event["acknowledge_missing"]) is bool and (not missing or event["acknowledge_missing"]))
            _require(all(key[0] != owner for key in open_flags))
        else:
            digest, day, p, page = event["source_hash"], event["date"], event["period"], event["page"]
            _require(day in dates and type(p) is int and 1 <= p <= 13)
            key = owner, digest, day, p
            if digest == "manual":
                _require(event["action"] == "slot" and page is None)
            else:
                _require((owner, digest) in real_owners and type(page) is int and page >= 0
                         and (digest not in page_counts or page < page_counts[digest]))
            if event["action"] == "slot":
                _require(type(event["marked"]) is bool)
                last_slot[key] = event["marked"]
            else:
                _require(type(event["open"]) is bool and event["open"] != (key in open_flags))
                if {"bbox", "page_width", "page_height"}.intersection(event):
                    from app.services.date_review import _placement
                    _require(bool(_placement(event)))
                if event["open"]:
                    open_flags[key] = page
                else:
                    _require(open_flags.pop(key) == page)
    for owner, fingerprint in state["confirmed"].items():
        event = latest.get(owner, {})
        _require(DIGEST.fullmatch(fingerprint) and event.get("action") == "member")
        # A locally edited source may leave a stale receipt; the workbench already
        # treats it as pending. A current receipt must cover the actual missing days.
        if members[owner]["confirmed"]:
            _require(event["missing_dates"] == members[owner]["missing_dates"])

    for key, rows in state.get("removed_slots", {}).items():
        owner, digest, day, period = key.rsplit("/", 3)
        _require(last_slot.get((owner, digest, day, int(period))) is False)
        for row in rows:
            _require((row["member_key"], row["source_hash"]) == (owner, digest)
                     and (row["week"], row["weekday"]) == dates[day] and int(period) in row["periods"])
    for row in data["blocks"] + data["removed_blocks"]:
        if row["source_hash"] == "manual":
            event = operations.get(row["block_id"].removeprefix("manual-"), {})
            _require(event.get("action") == "slot" and event["marked"] and event["source_hash"] == "manual"
                     and event["member_key"] == row["member_key"] and event["date"] == row["date"]
                     and event["period"] in row["periods"])


def _restore(document, calendar):
    from core import schedule_core

    files = document["files"]
    _require(isinstance(files, list) and 0 < len(files) <= MAX_INPUT_FILES)
    bindings = set()
    for row in files:
        _filename(row["file_name"])
        _require(not row["source_hash"] or DIGEST.fullmatch(row["source_hash"]))
        bindings.add((row["file_name"], row["source_hash"]))
    raw = document["snapshot"]
    # Reproject inbound records. Extra fields, including source_path and bytes_data, cannot reach a job.
    snapshot = _project(raw, files)
    _require(snapshot == raw)
    _normalize_manual_pages(snapshot)
    data = snapshot["generation"]
    expected_signature = _signature(calendar)
    if snapshot["parser_signature"] != expected_signature:
        raise ValueError("云端课表与此版本的识别规则不兼容，请使用兼容版本打开。")
    sources = {(s["file_name"], s["content_hash"]) for s in data["sources"]}
    _require(sources and sources <= bindings and all(DIGEST.fullmatch(digest) for _, digest in sources))
    _require(all(s["kind"] in {"中方", "英方"} for s in data["sources"]))
    date_review = snapshot.get("date_review")
    blocks = data["blocks"] + data["removed_blocks"]
    real_owners = {(row.get("member_key") or row["name"], row["source_hash"]) for row in blocks
                   if (row["source_file"], row["source_hash"]) in sources}
    manual_owners = {owner for owner, _ in real_owners}
    prior_rows = [row for rows in (date_review or {}).get("removed_slots", {}).values() for row in rows]
    for row in blocks + prior_rows:
        manual = (date_review is not None and row["source_hash"] == "manual" and
                  row["source_file"] == "人工核实" and row["source"] == "人工核实" and
                  row.get("member_key") in manual_owners and row.get("page") is None)
        _require((manual or (row["source_file"], row["source_hash"]) in sources) and row["block_id"] and row["name"])
        _require(type(row["week"]) is int and -1000 < row["week"] < 1000 and row["weekday"] in WEEKDAYS)
        _require(row["periods"] and all(type(p) is int and 1 <= p <= 13 for p in row["periods"]))
    _require(all(item.get("severity") in {"info", "warning", "error"} and type(item.get("confirmed")) is bool
                 and not (item["severity"] == "error" and (item["confirmed"] or not item["blocks_export"]))
                 for item in snapshot["issues"]))
    _require(all(isinstance(v, str) and DIGEST.fullmatch(v) for v in snapshot["verified_items"]))
    for correction in snapshot["corrections"]:
        _require(correction["parser_signature"] == expected_signature and
                 correction["source_hash"] in {digest for _, digest in sources})
    calendar_start = date.fromisoformat(calendar["semester_start_date"])
    rows = data["calendar_rows"]
    _require(0 < len(rows) <= 7 * calendar["teaching_weeks"])
    for row in rows:
        week = row["week"]
        _require(type(week) is int and 1 <= week <= calendar["teaching_weeks"] and row["weekday"] in WEEKDAYS)
        offset = (week - 1) * 7 + WEEKDAYS.index(row["weekday"])
        _require(row["date"] == (calendar_start + timedelta(days=offset)).isoformat())
    _require(len({(row["week"], row["weekday"]) for row in rows}) == len(rows))
    _require(all(type(w) is int for w in data["weeks"]) and len(set(data["weeks"])) == len(data["weeks"]))
    if date_review is not None:
        _check_date_review(date_review, data, real_owners)
        # Portable batches omit derived rosters and occupancy. Reconstruct them
        # before the dated preview validates and selects its first calendar range.
        data["students"] = sorted(manual_owners)
        data["occupancy"] = [
            {"week": week, "weekday": weekday, "period": period, "members": sorted(members)}
            for (week, weekday, period), members in schedule_core.build_occupancy(data["blocks"]).items()
        ]
    from app.services.date_review import reconcile_saved_date_issues
    reconcile_saved_date_issues(snapshot, files)
    workflow = restore_workflow(snapshot, schedule_core)
    original_rows = [dict(row) for row in data["blocks"]]
    _refresh_derived(workflow, schedule_core)
    restored = snapshot_workflow(workflow)
    # Keep any narrower coverage recorded by the source parser. A downloaded
    # document cannot extend the weeks supported by the locally rebuilt model.
    _require(set(data["weeks"]) <= set(restored["generation"]["weeks"]))
    restored["generation"]["weeks"] = data["weeks"]
    # A calendar refresh may add date/time labels or replace a printed date.
    # Retain the source rows; calculations above use their checked slot keys.
    restored["generation"]["blocks"] = original_rows
    # Recalculation can alter diagnostic wording. Keep all upstream gates and confirmations,
    # and add independently discovered structural and calendar errors.
    issues_by_id = {i["issue_id"]: i for i in snapshot["issues"]}
    for issue in restored["issues"]:
        issues_by_id[issue["issue_id"]] = issue
    restored["issues"] = list(issues_by_id.values())
    restored["quality_state"] = quality_state([ParseIssue(**i) for i in restored["issues"]])
    _require(restored["quality_state"] == snapshot["quality_state"])
    return restored


def import_batch(store, document, resource_paths):
    """Restore to the caller's account-specific store and calendar, never to a guest profile."""
    try:
        _require(document["format"] in {FORMAT, DATED_FORMAT} and
                 (document["format"] == DATED_FORMAT) == ("date_review" in document["snapshot"]))
        body = {k: document[k] for k in ("format", "batch_id", "calendar", "files", "snapshot")}
        _require(hmac.compare_digest(_checksum(body), document["checksum"]))
        batch_id = str(UUID(body["batch_id"]))
        calendar = normalize_calendar_settings(body["calendar"])
        snapshot = _restore(body, calendar)
    except (AttributeError, KeyError, TypeError, OverflowError, IndexError) as exc:
        raise ValueError(INVALID) from exc
    serialized = _json(snapshot)
    files = body["files"]
    old_calendar = load_calendar_settings(resource_paths)
    changed_calendar = calendar != old_calendar
    wrote_calendar = False
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id FROM jobs WHERE json_extract(request_json,'$.cloud_batch_id')=?
                                AND result_json=? ORDER BY rowid DESC LIMIT 1""", (batch_id, serialized)).fetchone()
            if row:
                job_id = row["id"]
            else:
                job_id = str(uuid4())
                now = utc_now()
                request = {"cloud_batch_id": batch_id, "file_count": len(files)}
                if "date_review" in snapshot:
                    request.update({key: snapshot["date_review"][key] for key in ("start_date", "end_date")})
                db.execute("""INSERT INTO jobs(id,status,created_at,updated_at,parser_signature,request_json)
                              VALUES(?,?,?,?,?,?)""", (job_id, "completed", now, now, snapshot["parser_signature"],
                                                      _json(request)))
                for f in files:
                    file_id = str(uuid4())
                    db.execute("""INSERT INTO job_files(id,job_id,source_path,file_name,source_hash,status,stage)
                                  VALUES(?,?,?,?,?,?,?)""", (file_id, job_id, "__cleared__:" + file_id, f["file_name"],
                                                            f["source_hash"], f["status"], f["stage"]))
                store.save_job_result(job_id, result_json=serialized, error="", quality_state=snapshot["quality_state"],
                                      current=len(files), total=len(files), issues=snapshot["issues"], connection=db)
                for issue in snapshot["issues"]:
                    if issue.get("confirmed"):
                        db.execute("UPDATE issues SET confirmed=1 WHERE job_id=? AND issue_id=?",
                                   (job_id, issue["issue_id"]))
                for c in snapshot["corrections"]:
                    fields = (c["source_hash"], c["block_id"], c["field"], _json(c["original_value"]),
                              _json(c["new_value"]), c["reason"], c["parser_signature"], c["operator_id"],
                              c["created_at"], int(c.get("stale", False)))
                    # A local correction can arrive back through the cloud under a different row id.
                    matches = db.execute("""SELECT original_json,new_json FROM corrections WHERE source_hash=?
                                            AND block_id=? AND field=? AND parser_signature=? AND created_at=?
                                            AND reason=? AND operator_id=?""",
                                         (fields[0], fields[1], fields[2], fields[6], fields[8], fields[5], fields[7]))
                    if not any(json.loads(m["original_json"]) == c["original_value"] and
                               json.loads(m["new_json"]) == c["new_value"] for m in matches):
                        db.execute("""INSERT OR IGNORE INTO corrections(id,source_hash,block_id,field,original_json,
                                      new_json,reason,parser_signature,operator_id,created_at,stale)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?)""", ("cloud-" + _checksum(fields), *fields))
            write_workspace(db, [f["file_name"] for f in files], False, job_id)
            if changed_calendar:
                save_calendar_settings(resource_paths, calendar)
                wrote_calendar = True
    except Exception:
        if wrote_calendar:
            save_calendar_settings(resource_paths, old_calendar)
        raise
    apply_calendar_settings(calendar)
    return {"ok": True, "job_id": job_id, "calendar": calendar}
