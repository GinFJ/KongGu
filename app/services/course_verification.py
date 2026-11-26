"""Job-local visual checks, bound to the current content of each printed course."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from app.services.desktop_serialization import json_safe
from core.legacy_adapter import hash_file
from core.signature import build_parser_signature


def _key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def review_blocks(blocks, inspections):
    """Prepare display geometry without mutating recognition data or block identities."""
    single_page = {i['source_hash'] for i in inspections if i.get('source_hash') and
                   i.get('status') == 'ready' and i.get('page_count') == 1}
    result = []
    for row in blocks:
        view = dict(row)
        if row.get('source_file'):
            view['source_file'] = Path(row['source_file']).name
        if row.get('page') is None and row.get('source_hash') in single_page:
            view['page'] = 0
        # Native text coordinates use PDF points. Raster/OCR coordinates need
        # the dimensions of the original rendered page to scale their pixels.
        if row.get('bbox') is not None:
            sized = all(_finite_number(row.get(k)) and row[k] > 0 for k in ('page_width', 'page_height'))
            if not _valid_bbox(row['bbox']) or (not sized and row.get('text_source') != '内嵌文本'):
                view.pop('bbox', None)
        result.append(view)
    return result


def _finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _valid_bbox(bbox):
    return (isinstance(bbox, (list, tuple)) and len(bbox) == 4 and
            all(_finite_number(v) for v in bbox) and bbox[2] > bbox[0] and bbox[3] > bbox[1])


def _periods(row):
    if row.get('periods'):
        return tuple(sorted(row['periods']))
    return (row['period'],) if row.get('period') is not None else ()


def _same_cell_period_groups(rows):
    by_week = {}
    for row in rows:
        by_week.setdefault((row.get('week'), row.get('date')), []).append(row)
    by_periods = {}
    for week_rows in by_week.values():
        periods = [p for row in week_rows for p in _periods(row)]
        if periods and len(set(periods)) == len(periods):
            # The parser emits one occupancy per period of the same printed course.
            # Show exactly those periods together, including any explicit gaps.
            by_periods.setdefault(tuple(sorted(periods)), []).extend(week_rows)
        else:
            # Overlapping records cannot establish a single consistent duration.
            for row in week_rows:
                by_periods.setdefault(_periods(row), []).append(row)
    return by_periods


def verification_queue(blocks, signature, verified):
    groups = {}
    for index, row in enumerate(blocks):
        bbox = row.get('bbox')
        fields = ('source_hash', 'source_file', 'member_key', 'name', 'department', 'role',
                  'course', 'weekday', 'page', 'page_width', 'page_height', 'bbox')
        binding = {field: row.get(field) for field in fields}
        if not (row.get('source_hash') and row.get('page') is not None and _valid_bbox(bbox)):
            # Missing coordinates cannot establish that two weeks came from the same place.
            binding['single'] = row.get('block_id') or index
        groups.setdefault(_key(json_safe(binding)), []).append(row)

    items = []
    for binding, cell in groups.items():
        for periods, rows in _same_cell_period_groups(cell).items():
            # Bind the fields in the visual check. Recalculation may populate a
            # legacy row's derived date/time without changing its reviewed slot.
            occurrences = sorted(_key(json_safe([r.get('block_id'), r.get('week'), _periods(r)]))
                                 for r in rows)
            digest = hashlib.sha256(_key([signature, binding, occurrences]).encode()).hexdigest()
            items.append(dict(item_id=digest, source_hash=rows[0].get('source_hash') or '',
                              source_file=rows[0].get('source_file') or '', periods=list(periods),
                              block_ids=[r.get('block_id') or '' for r in rows],
                              weeks=sorted({int(r['week']) for r in rows if r.get('week') is not None}),
                              confirmed=digest in verified))
    confirmed = sum(item['confirmed'] for item in items)
    return dict(total=len(items), confirmed=confirmed, pending=len(items)-confirmed, items=items)


def workflow_verification(workflow):
    generation = workflow.generation_result
    rows = review_blocks(generation.blocks, generation.pdf_inspections)
    return verification_queue(rows, workflow.process_result.parser_signature, workflow.verified_items)


def verify_course(store, job_id, item_id, revision, *, compact=False):
    from app.services.review_operations import review_revision
    from app.services.review_service import get_review_payload

    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT result_json FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or not row['result_json']:
            raise ValueError('未找到可核验的课表。')
        snapshot = json.loads(row['result_json'])
        if snapshot['parser_signature'] != build_parser_signature().digest:
            raise ValueError('学期设置或解析规则已变化，请重新生成课表。')
        verified = set(snapshot.get('verified_items', []))
        generation = snapshot['generation']
        rows = review_blocks(generation['blocks'], generation.get('pdf_inspections', []))
        queue = verification_queue(rows, snapshot['parser_signature'], verified)
        item = next((i for i in queue['items'] if i['item_id'] == item_id), None)
        old_revision = review_revision(snapshot)
        if not item or (item_id not in verified and revision != old_revision):
            raise ValueError('核验内容已变化，请刷新后重新对照原件。')
        if item_id not in verified:
            digest = item['source_hash']
            paths = [p for p in store.source_paths_for_job(job_id) if Path(p).name == Path(item['source_file']).name]
            if not digest or not any(Path(p).is_file() and hash_file(p) == digest for p in paths):
                raise ValueError('原件已移动或内容变化，请重新导入后核验。')
            verified.add(item_id)
            snapshot['verified_items'] = sorted(verified)
            store.update_job(job_id, connection=db, result_json=_key(snapshot))
            store.add_audit_event(job_id, 'review.course_verified', {'item_id': item_id}, connection=db)
        if compact and old_revision == revision:
            confirmed = queue['confirmed'] + int(not item['confirmed'])
            return {"ok": True, "job_id": job_id, "item_id": item_id, "previous_revision": old_revision,
                    "revision": review_revision(snapshot) if not item['confirmed'] else old_revision,
                    "verification": {"total": queue['total'], "confirmed": confirmed, "pending": queue['total'] - confirmed}}
    return get_review_payload(store, job_id)
