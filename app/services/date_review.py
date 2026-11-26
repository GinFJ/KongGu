"""A local batch has one inclusive date range and one decision per member."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
import json
import math
from pathlib import Path
from uuid import UUID

from app.services.availability_preview_service import align_slot_dates, build_availability_preview
from app.services.course_verification import review_blocks
from app.services.review_operations import lock_review_revision, review_revision, source_identity
from app.services.state_store import utc_now
from core.legacy_adapter import conflicting_course_slots, course_blocks_from_legacy, hash_file
from core.models import ParseIssue
from core.quality import quality_state
from core.signature import build_parser_signature


# Date coverage is reconciled per member in this flow. These are the only old
# whole-batch findings replaced by the per-date missing-material checklist.
COVERAGE_ISSUES = {'MEMBER_SCHEDULE_INCOMPLETE', 'TIMETABLE_COVERAGE_EMPTY', 'TIMETABLE_COVERAGE_LIMIT'}
WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def date_bounds(request):
    if 'start_date' not in request and 'end_date' not in request:
        return None
    try:
        start = date.fromisoformat(request['start_date'])
        end = date.fromisoformat(request['end_date'])
        if start.isoformat() != request['start_date'] or end.isoformat() != request['end_date'] or end < start:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError('请选择有效的开始和结束日期，结束日期不能早于开始日期。') from None
    return dict(start_date=start.isoformat(), end_date=end.isoformat())


def selected_calendar(frame, state):
    dates = frame['date'].astype(str).str.slice(0, 10)
    selected = frame.loc[dates.between(state['start_date'], state['end_date'])].copy()
    count = (date.fromisoformat(state['end_date']) - date.fromisoformat(state['start_date'])).days + 1
    if len(selected) != count or selected['date'].astype(str).str[:10].nunique() != count:
        raise ValueError('所选日期未完整落在本批次校历内，请检查学期设置及合表起止日期。')
    return selected


def _dated_conflicts(blocks, days, confirmed_ids, legacy_confirmed=()):
    groups = defaultdict(list)
    for block in blocks:
        if (block.week, block.weekday) in days:
            groups[block.member_key].append(block)
    conflicts = []
    for owner, courses in groups.items():
        for (week, weekday, period), rows, labels, old_error in conflicting_course_slots(courses):
            day = days[week, weekday]
            evidence = sorted((r.source_hash or '', r.block_id, r.course or '') for r in rows)
            anchor = min(rows, key=lambda r: (r.source_hash in {None, 'manual'}, r.block_id))
            issue_id = review_revision(['SCHEDULE_CONFLICT', owner, day, period, evidence])[:20]
            issue = ParseIssue(code='SCHEDULE_CONFLICT', field='periods', source_file=anchor.source_file,
                source_hash=anchor.source_hash, block_id=anchor.block_id, blocks_export=True,
                message=f"{anchor.name} {day} 第{period}节课程冲突：{'、'.join(labels)}",
                suggestion='核对该成员这一节的原件，修正色块或确认冲突。',
                issue_id=issue_id, confirmed=issue_id in confirmed_ids)
            conflicts.append((issue, old_error.to_user_message()))
    # 0.13.0 displayed only the first conflict per member. Carry over only the
    # exact warning the user saw, and only if it identifies a single conflict.
    counts = Counter(old_message for _, old_message in conflicts)
    for issue, old_message in conflicts:
        if old_message in legacy_confirmed and counts[old_message] == 1:
            issue.confirmed = True
    return [issue for issue, _ in conflicts]


def reconcile_saved_date_issues(snapshot, files):
    """Recover omitted-source gates and upgrade legacy dated conflict warnings."""
    state = snapshot.get('date_review')
    data = snapshot.get('generation')
    if not data or 'issues' not in snapshot:
        return False
    old = snapshot['issues']
    issues = list(old)
    if state and any(i['code'] == 'SCHEDULE_CONFLICT' and not i.get('block_id') for i in old):
        days = {(int(r['week']), r['weekday']): str(r['date'])[:10] for r in data['calendar_rows']
                if state['start_date'] <= str(r['date'])[:10] <= state['end_date']}
        confirmed = [i for i in old if i.get('confirmed') and i['code'] == 'SCHEDULE_CONFLICT']
        conflicts = _dated_conflicts(course_blocks_from_legacy(data['blocks']), days,
            {i['issue_id'] for i in confirmed}, {i['message'] for i in confirmed if not i.get('block_id')})
        issues = [i for i in old if i['code'] != 'SCHEDULE_CONFLICT'] + [asdict(i) for i in conflicts]
    from app.services.pdf_source_service import duplicate_identity_error
    by_hash = defaultdict(list)
    for source in data['sources']:
        by_hash[source['content_hash']].append(source)
    existing = {i.get('source_file') for i in issues if i['code'] == 'INPUT_PREFLIGHT_FAILED'}
    for row in files:
        name = row['file_name']
        matches = by_hash.get(row.get('source_hash'), [])
        if not matches or any(s['file_name'] == name for s in matches) or name in existing:
            continue
        for source in matches:
            message = duplicate_identity_error(source['file_name'], name, source['kind'])
            if message:
                issues.append(asdict(ParseIssue(code='INPUT_PREFLIGHT_FAILED', severity='error', message=message,
                    source_file=name, suggestion='核对成员身份、课表类型和文件来源后重新导入整批课表。', blocks_export=True)))
                existing.add(name)
                break
    if issues == old:
        return False
    snapshot['issues'] = issues
    snapshot['quality_state'] = quality_state(ParseIssue(**i) for i in issues)
    return True


def apply_date_scope(workflow, schedule_core, confirmed_issue_ids=()):
    """Project the current occupancy onto the agreed days, including partial weeks."""
    state = workflow.date_review
    if state is None:
        return
    generation = workflow.generation_result
    selected = selected_calendar(generation.calendar_df, state)
    weeks = sorted({int(w) for w in selected['week']})
    timetable = schedule_core.default_timetable()
    periods = [int(p) for p in timetable['period']]
    slots = schedule_core.build_slot_table(generation.occupancy, generation.students, weeks, WEEKDAYS, periods)
    slots = align_slot_dates(slots, generation.calendar_df)
    generation.all_slot_df = slots.loc[slots['date'].astype(str).str[:10].between(state['start_date'], state['end_date'])].copy()
    generation.weeks = weeks
    preview = build_availability_preview(occupancy=generation.occupancy, students=generation.students,
        weeks=weeks, weekdays=WEEKDAYS, calendar_df=generation.calendar_df, timetable_df=timetable)
    scope = set(selected['date'].astype(str).str[:10])
    process = workflow.process_result
    process.slots = [s for s in preview.slots if str(s.date)[:10] in scope]
    workflow.preview_df = (preview.free_df.copy() if preview.free_df.empty
                           else preview.free_df.loc[preview.free_df['日期'].isin(scope)].copy())
    process.summary.slot_count = len(process.slots)
    # A dated course warning outside this assignment does not concern its review.
    outside = {b.get('block_id') for b in generation.blocks
               if b.get('date') and str(b['date'])[:10] not in scope}
    confirmed = {i.issue_id for i in process.issues if i.confirmed} | set(confirmed_issue_ids)
    legacy = {i.message for i in process.issues if i.code == 'SCHEDULE_CONFLICT' and i.confirmed and not i.block_id}
    days = {(int(r.week), r.weekday): str(r.date)[:10] for r in selected.itertuples()}
    process.issues = [i for i in process.issues if i.code not in COVERAGE_ISSUES and i.code != 'SCHEDULE_CONFLICT'
                      and not (i.block_id and i.block_id in outside and i.severity == 'warning')]
    process.issues.extend(_dated_conflicts(generation.course_blocks, days, confirmed, legacy))
    process.quality_state = quality_state(process.issues)


def _periods(row):
    return row.get('periods') or [row['period']]


def _rows_by_member(data):
    groups = {}
    for row in data['blocks'] + data.get('removed_blocks', []):
        key = row.get('member_key') or row.get('name')
        if key:
            groups.setdefault(key, []).append(row)
    return groups


def _flag_key(event):
    return tuple(event[field] for field in ('member_key', 'source_hash', 'date', 'period'))


def active_flags(state):
    active = {}
    for event in state.get('log', []):
        if event['action'] == 'flag':
            key = _flag_key(event)
            if event['open']:
                active[key] = event
            else:
                active.pop(key, None)
    return active


def review_members(state, data):
    """Bind each receipt to its source content, missing dates and active courses."""
    groups = _rows_by_member(data)
    sources = {s['content_hash']: s for s in data['sources']}
    coverages = {i['source_hash']: set(i['date_coverage']['dates'])
                 for i in data.get('pdf_inspections', []) if i.get('date_coverage') is not None}
    dates_by_slot = {(int(r['week']), r['weekday']): str(r['date'])[:10] for r in data['calendar_rows']}
    dates = {d for d in dates_by_slot.values() if state['start_date'] <= d <= state['end_date']}
    active_ids = {id(row) for row in data['blocks']}
    flagged_members = {key[0] for key in active_flags(state)}
    result = []
    for key, rows in sorted(groups.items()):
        hashes = sorted({r['source_hash'] for r in rows if r.get('source_hash') in sources})
        missing = set()
        for kind in ('中方', '英方'):
            matched = [h for h in hashes if sources[h]['kind'] == kind]
            coverage = set()
            for digest in matched:
                if digest in coverages:
                    coverage.update(coverages[digest])
                elif kind == '中方' and data.get('layout_profiles', {}).get(digest) in {
                    'cdut_undergrad_full_term_cn', 'cdut_legacy_chinese_web'
                }:
                    coverage.update(dates)
            missing.update(dates - coverage)
        evidence = []
        for row in rows:
            day = dates_by_slot.get((row.get('week'), row.get('weekday')))
            if id(row) in active_ids and day in dates:
                evidence.append([row['block_id'], row.get('course'), day, list(_periods(row)), row.get('source_hash')])
        fingerprint = review_revision([key, state['start_date'], state['end_date'], hashes, sorted(missing),
                                       sorted(evidence, key=lambda e: str(e[0]))])
        person = rows[0]
        result.append(dict(member_key=key, name=person.get('name') or key, department=person.get('department') or '',
            role=person.get('role') or '', source_hashes=hashes, missing_dates=sorted(missing),
            confirmed=key not in flagged_members and state.get('confirmed', {}).get(key) == fingerprint,
            fingerprint=fingerprint))
    return result


def date_review_view(state, data):
    members = review_members(state, data)
    for member in members:
        member.pop('fingerprint')
    calendar = [dict(week=int(row['week']), weekday=row['weekday'], date=str(row['date'])[:10])
                for row in data['calendar_rows'] if state['start_date'] <= str(row['date'])[:10] <= state['end_date']]
    removed = []
    for key, rows in state.get('removed_slots', {}).items():
        _, day, period = key.rsplit('/', 2)
        for row in rows:
            removed.append({**row, 'date': day, 'period': int(period), 'periods': [int(period)]})
    return dict(start_date=state['start_date'], end_date=state['end_date'], members=members,
                calendar=calendar, log=state.get('log', []), flags=list(active_flags(state).values()),
                removed_positions=review_blocks(removed, data.get('pdf_inspections', [])))


def workflow_view(workflow):
    gen = workflow.generation_result
    return date_review_view(workflow.date_review, dict(
        sources=[dict(content_hash=s.content_hash, kind=s.kind) for s in gen.model_sources],
        calendar_rows=gen.calendar_df.to_dict('records'), blocks=gen.blocks, removed_blocks=gen.removed_blocks,
        pdf_inspections=gen.pdf_inspections, layout_profiles={r.source.content_hash: r.layout_profile for r in gen.file_records}))


def member_counts(view):
    total = len(view['members'])
    confirmed = sum(m['confirmed'] for m in view['members'])
    return dict(total=total, confirmed=confirmed, pending=total-confirmed)


def _source_path(job, digest):
    path = next((f.get('source_path') for f in job['files'] if f.get('source_hash') == digest and f.get('source_path')), None)
    if not path or not Path(path).is_file() or hash_file(path) != digest:
        raise ValueError('原件已移动或内容变化，请重新关联当前原件后再核对。')
    return Path(path)


def _placement(request):
    if request.get('bbox') is None:
        return {}
    bbox = request['bbox']
    width, height = request.get('page_width'), request.get('page_height')
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not all(
        type(n) in (int, float) and math.isfinite(n) for n in [width, height, *bbox]
    ) or not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
        raise ValueError('原件标记位置不在当前页面内，请重新定位。')
    return dict(bbox=list(bbox), page_width=width, page_height=height)


def _cell_target(state, data, member, event, request, job, schedule_core):
    digest, day, period = event['source_hash'], event['date'], event['period']
    if type(period) is not int or period not in set(schedule_core.default_timetable()['period']):
        raise ValueError('请选择有效的节次。')
    calendar = next((r for r in data['calendar_rows'] if str(r['date'])[:10] == day), None)
    if not calendar or not state['start_date'] <= day <= state['end_date']:
        raise ValueError('该日期不在本次合表范围内。')
    manual = digest == 'manual'
    if manual and event['action'] == 'flag':
        raise ValueError('请在当前成员的真实原件上标记课表问题。')
    if not manual and digest not in member['source_hashes']:
        raise ValueError('所选原件不属于该成员。')
    if manual:
        identity = {k: member[k] for k in ('name', 'department', 'role', 'member_key')}
        source = {'kind': '人工核实', 'file_name': '人工核实'}
        page, placement = None, {}
    else:
        source = next(s for s in data['sources'] if s['content_hash'] == digest)
        identity = source_identity(data, source)
        if identity['member_key'] != member['member_key']:
            raise ValueError('原件成员身份不一致。')
        import fitz
        page = request.get('page')
        with fitz.open(_source_path(job, digest)) as pdf:
            if type(page) is not int or not 0 <= page < pdf.page_count:
                raise ValueError('请选定正确的原件页码。')
        placement = _placement(request)
    return source, identity, page, placement, int(calendar['week']), calendar['weekday']


def _edit_slot(state, data, member, event, target):
    digest, day, period, marked = (event[k] for k in ('source_hash', 'date', 'period', 'marked'))
    if type(marked) is not bool:
        raise ValueError('请选择有效的标记状态。')
    source, identity, page, placement, week, weekday = target
    active = [b for b in data['blocks'] if b.get('member_key') == member['member_key'] and
              b.get('source_hash') == digest and b.get('week') == week and
              b.get('weekday') == weekday and period in _periods(b)]
    if bool(active) == marked:
        raise ValueError('该格已是所选状态，请刷新核对结果。')
    key = f'{member["member_key"]}/{digest}/{day}/{period}'
    saved = state.setdefault('removed_slots', {})
    removed = data.setdefault('removed_blocks', [])
    if not marked:
        saved[key] = deepcopy(active)
        for row in active:
            keep = [p for p in _periods(row) if p != period]
            if keep:
                row.update(periods=keep, period=keep[0])
            else:
                data['blocks'].remove(row)
                removed.append(row)
        return
    original = saved.pop(key, [])
    if not original:
        original = [dict(**identity, block_id='manual-' + event['operation_id'], source_hash=digest,
            source_file=source['file_name'], source_type=source['kind'], source=source['kind'], kind=source['kind'],
            course='人工标记有课', week=week, weekday=weekday, date=day, page=page, text_source='人工修正',
            provenance=dict(manual_entry=True), **placement)]
    for row in original:
        current = next((b for b in data['blocks'] if b['block_id'] == row['block_id']), None)
        if current is None:
            data['blocks'].append({**row, 'periods': [period], 'period': period})
            removed[:] = [b for b in removed if b['block_id'] != row['block_id']]
        else:
            periods = sorted({*_periods(current), period})
            current.update(periods=periods, period=periods[0])


def normalize_manual_slot_page(event):
    """0.13.0 used page zero as a placeholder for a slot without a PDF."""
    if (event.get('action') == 'slot' and event.get('source_hash') == 'manual'
            and type(event.get('page')) is int and event['page'] == 0):
        return {**event, 'page': None}
    return event


def update_date_review(store, schedule_core, job_id, request, action):
    from app.services.review_service import _refresh_derived, get_review_payload
    from app.services.workflow_persistence import restore_workflow, snapshot_workflow
    job = store.get_job(job_id, include_sensitive_paths=True)
    if not job or not (job.get('result') or {}).get('date_review'):
        raise ValueError('此任务没有按日期建立的核对清单。')
    snapshot = job['result']
    state, data = snapshot['date_review'], snapshot['generation']
    if snapshot['parser_signature'] != build_parser_signature().digest:
        raise ValueError('学期设置或解析规则已变化，请重新生成课表。')
    try:
        operation_id = str(UUID(str(request.get('operation_id'))))
    except ValueError:
        raise ValueError('缺少有效操作编号。') from None
    event = dict(operation_id=operation_id, action=action, member_key=request.get('member_key'))
    if action in {'slot', 'flag'}:
        event.update({k: request.get(k) for k in ('source_hash', 'date', 'period', 'page')})
        if action == 'slot':
            event['marked'] = request.get('marked')
            event = normalize_manual_slot_page(event)
            if event['source_hash'] == 'manual' and event['page'] is not None:
                raise ValueError('人工核实的节次不应带有原件页码。')
        else:
            if type(request.get('open')) is not bool:
                raise ValueError('请选择标记问题或完成核实。')
            event.update(open=request['open'], **_placement(request))
    elif action == 'member':
        event['acknowledge_missing'] = request.get('acknowledge_missing') is True
    else:
        raise ValueError('不支持的核对操作。')
    previous = next((e for e in state['log'] if e['operation_id'] == operation_id), None)
    if previous:
        previous = normalize_manual_slot_page(previous)
        if any(previous.get(k) != v for k, v in event.items()):
            raise ValueError('操作编号已用于另一项核对。')
        return get_review_payload(store, job_id)
    revision = request.get('revision')
    if revision != review_revision(snapshot):
        raise ValueError('核对结果已经变化，请刷新后再操作。')
    member = next((m for m in review_members(state, data) if m['member_key'] == event['member_key']), None)
    if not member:
        raise ValueError('本批次没有这位成员。')
    if action in {'slot', 'flag'}:
        target = _cell_target(state, data, member, event, request, job, schedule_core)
        if action == 'slot':
            _edit_slot(state, data, member, event, target)
        else:
            previous_flag = active_flags(state).get(_flag_key(event))
            if bool(previous_flag) == event['open']:
                raise ValueError('该格的待核实状态已经变化，请刷新。')
            if previous_flag and previous_flag['page'] != event['page']:
                raise ValueError('该疑点位于另一页，请先定位原格再处理。')
        state['confirmed'].pop(member['member_key'], None)
    else:
        if any(key[0] == member['member_key'] for key in active_flags(state)):
            raise ValueError('请先处理该成员在原件上标记的待核实问题。')
        if not member['department'] or not member['role'] or not member['source_hashes']:
            raise ValueError('请先核实该成员身份和原件归属。')
        if member['missing_dates'] and not event['acknowledge_missing']:
            raise ValueError('本次区间有日期缺资料，请核实实际课程后再确认。')
        for digest in member['source_hashes']:
            _source_path(job, digest)
        hashes = set(member['source_hashes'])
        filenames = {s['file_name'] for s in data['sources'] if s['content_hash'] in hashes}
        blockers = [i['message'] for i in snapshot['issues'] if i.get('blocks_export') and not i.get('confirmed')
                    and (i.get('source_hash') in hashes or i.get('source_file') in filenames
                         or not (i.get('source_hash') or i.get('source_file')))]
        if blockers:
            raise ValueError('请先处理课表问题：' + blockers[0])
        state['confirmed'][member['member_key']] = member['fingerprint']
        event['missing_dates'] = member['missing_dates']
    event['created_at'] = utc_now()
    state['log'].append(event)
    if action == 'slot':
        workflow = restore_workflow(snapshot, schedule_core)
        _refresh_derived(workflow, schedule_core)
        snapshot = snapshot_workflow(workflow)
    with store.connect() as connection:
        lock_review_revision(connection, job_id, revision)
        store.save_job_result(job_id, result_json=json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')),
            error=job.get('error') or '', quality_state=snapshot['quality_state'], current=job['current'],
            total=job['total'], issues=snapshot['issues'], connection=connection)
        store.add_audit_event(job_id, 'review.' + action, {'operation_id': operation_id}, connection=connection)
    return get_review_payload(store, job_id)
