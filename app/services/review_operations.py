"""Audited course additions, reversible removals and region decisions.

Only active blocks enter occupancy. Removed blocks remain in the snapshot and
operation journal, so removing the final course cannot remove its member.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import UUID

from core.identity import identity_from_filename
from core.legacy_adapter import hash_file
from core.models import CorrectionRecord, MemberSchedule, ParseIssue
from core.signature import build_parser_signature
from app.services.state_store import utc_now

OPERATION_FIELD = "course_operation"
OPERATION_LABELS = dict(add='补录漏课', delete='删除误识别记录', restore='恢复已删除记录',
                        restore_region='恢复误排除区域', exclude_region='排除非课程区域', undo='撤销课程操作')


def correction_export_values(correction):
    if correction.field != OPERATION_FIELD:
        return dict(字段=correction.field, 原值=json.dumps(correction.original_value, ensure_ascii=False),
                    新值=json.dumps(correction.new_value, ensure_ascii=False))
    operation = correction.new_value
    def describe(rows):
        return '\n'.join(f"{b.get('source_file', '')} "
            f"{('第'+str(int(b['page'])+1)+'页') if b.get('page') is not None else '原件页码未记录'}｜"
            f"{b.get('course', '')}｜第{b.get('week')}周{b.get('weekday')}｜"
            f"第{'、'.join(map(str, b.get('periods') or [b.get('period')]))}节"
            for b in rows if b) or '无课程占用'
    return dict(字段=OPERATION_LABELS[operation['action']],
                原值=describe([c['before'] for c in operation['changes']]),
                新值=describe([c['after'] for c in operation['changes']]))


def review_revision(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def lock_review_revision(connection, job_id, revision):
    """Prevent a stale correction or issue decision from replacing newer review work."""
    connection.execute('BEGIN IMMEDIATE')
    current = connection.execute('SELECT result_json FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not current or not current['result_json'] or review_revision(json.loads(current['result_json'])) != revision:
        raise ValueError('结果已经变化，请刷新复核页面后重试。')


def source_identity(data, source):
    rows = [b for b in data.get('blocks', []) + data.get('removed_blocks', [])
            if b.get('source_hash') == source['content_hash']]
    identities = {(b.get('name'), b.get('department'), b.get('role'), b.get('member_key')) for b in rows}
    if len(identities) == 1:
        name, department, role, key = next(iter(identities))
        if name and department and role and key:
            return dict(name=name, department=department, role=role, member_key=key)
    if identities:
        raise ValueError('此来源的成员身份不完整或存在冲突，请先统一文件名并重新导入。')
    identity = identity_from_filename(source['file_name'])
    if identity.confirmation_required:
        raise ValueError('请先在文件名中明确姓名、部门和职务，再重新导入。')
    return dict(name=identity.name, department=identity.department, role=identity.role,
                member_key=identity.member_key)


def region_choices(snapshot):
    from app.services.desktop_workflow import _notice_consensus_issue
    data = snapshot.get('generation') or {}
    result = []
    for inspection in data.get('pdf_inspections', []):
        for note in inspection.get('nonclass_consensus', []):
            if note.get('kind') != 'same_page_nonclass_consensus':
                continue
            issue = _notice_consensus_issue(inspection['source_file'], inspection['source_hash'], note)
            result.append({**deepcopy(note), 'region_id': issue.issue_id,
                           'source_hash': inspection['source_hash'], 'source_file': inspection['source_file']})
    return result


def restore_empty_source_membership(generation):
    """A reviewed removal is evidence of a submitted source, never a parse fix."""
    sources = {s.content_hash: s for s in generation.model_sources}
    by_key = {m.member_key: m for m in generation.member_schedules}
    for block in generation.removed_blocks:
        source = sources.get(block.get('source_hash'))
        if not source or source.file_name != block.get('source_file'):
            continue
        key = block.get('member_key') or block.get('name')
        member = by_key.get(key)
        if member is None:
            member = MemberSchedule(name=block['name'], department=block.get('department'),
                                    role=block.get('role'), member_key=key)
            generation.member_schedules.append(member)
            by_key[key] = member
        if source.kind == '中方':
            member.has_chinese = True
            member.chinese_status = '已导入'
        else:
            member.has_english = True
            member.english_status = '已导入'
        if member.has_chinese and member.has_english:
            member.status = '完整'
        if not member.blocks:
            member.name = block['name']
            member.department = block.get('department')
            member.role = block.get('role')
            member.remark = '已人工核对并移除课程记录，原件及修正历史保留'
        for record in generation.file_records:
            if record.source.content_hash == source.content_hash and not record.block_count and not record.error:
                record.status = '人工核对无课程'
                record.quality_state = 'accepted'
                record.member_name = block['name']


def _binding(data, source):
    return dict(source_hash=source['content_hash'], file_name=source['file_name'],
                kind=source['kind'], identity=source_identity(data, source))


def _periods(block):
    return set(block.get('periods') or [block.get('period')])


def _block_state(block):
    if block is None:
        return None
    return {**{k: block.get(k) for k in ('block_id','source_hash','source_file','member_key','course','week','weekday')},
            'periods': sorted(_periods(block))}


def _new_block(data, source, fields, operation_id, page_count):
    from app.services.review_service import _normalize_value
    identity = source_identity(data, source)
    course = str(fields.get('course') or '').strip()
    if not course or len(course) > 500:
        raise ValueError('请填写原件中的课程名称或明确的占用说明（不超过 500 字）。')
    week = _normalize_value('week', fields.get('week'))
    weekday = _normalize_value('weekday', fields.get('weekday'))
    periods = _normalize_value('periods', fields.get('periods', []))
    page = int(fields.get('page', -1))
    if not 0 <= page < page_count:
        raise ValueError('页码不在这份原件的范围内。')
    calendar = {(int(r['week']), r['weekday']): str(r['date']) for r in data.get('calendar_rows', [])}
    if (week, weekday) not in calendar:
        raise ValueError('所填周次、星期不在当前校历中，请先核对学期设置。')
    for row in data.get('blocks', []):
        if (row.get('source_hash') == source['content_hash'] and row.get('week') == week
                and row.get('weekday') == weekday and _periods(row) & set(periods)):
            raise ValueError('这份原件的该时段已有课程记录，请修改已有记录，避免重复补录。')
    return dict(**identity, block_id='manual-' + operation_id, source_file=source['file_name'],
                source_hash=source['content_hash'], source_type=source['kind'], source=source['kind'], kind=source['kind'],
                course=course, week=week, weekday=weekday, date=calendar[(week, weekday)],
                period=periods[0], periods=periods, page=page, text_source='人工修正',
                provenance=dict(manual_entry=True, operation_id=operation_id))


def _change(data, before, after):
    block_id = (before or after)['block_id']
    data['blocks'] = [b for b in data.get('blocks', []) if b['block_id'] != block_id]
    data['removed_blocks'] = [b for b in data.get('removed_blocks', []) if b['block_id'] != block_id]
    if after is not None:
        data['blocks'].append(deepcopy(after))
    elif before is not None:
        data['removed_blocks'].append(deepcopy(before))


def _apply_operation(snapshot, operation):
    data = snapshot['generation']
    for change in operation['changes']:
        _change(data, change['before'], change['after'])
    # Occupancy changed: recheck other warnings against this result. Only the
    # region explicitly decided by this operation is resolved here.
    for issue in snapshot.get('issues', []):
        if issue.get('severity') == 'warning' and issue.get('source_hash') in {None, '', operation['binding']['source_hash']}:
            issue['confirmed'] = issue['issue_id'] == operation.get('region_id')


def mutate_courses(*, store, schedule_core, job_id, request):
    from app.services.review_service import _refresh_derived, get_review_payload
    from app.services.workflow_persistence import restore_workflow, snapshot_workflow
    reason = str(request.get('reason') or '').strip()
    if not reason or len(reason) > 2000:
        raise ValueError('请填写修正原因（不超过 2000 字）。')
    operation_id = str(request.get('operation_id') or '')
    try:
        UUID(operation_id)
    except ValueError:
        raise ValueError('缺少有效操作编号，请刷新复核页面后重试。') from None
    job = store.get_job(job_id, include_sensitive_paths=True)
    if not job or not job.get('result'):
        raise ValueError('未找到可修正的解析任务。')
    snapshot = deepcopy(job['result'])
    for previous in snapshot.get('corrections', []):
        if previous['field'] == OPERATION_FIELD and previous['new_value'].get('operation_id') == operation_id:
            return get_review_payload(store, job_id)
    if snapshot['parser_signature'] != build_parser_signature().digest:
        raise ValueError('学期设置或解析规则已变化，请重新生成后修正。')
    if request.get('revision') != review_revision(snapshot):
        raise ValueError('结果已经变化，请刷新复核页面后重试。')
    data = snapshot['generation']
    matches = [s for s in data['sources'] if s['content_hash'] == request.get('source_hash')]
    if len(matches) != 1:
        raise ValueError('无法唯一确定原件，请重新导入。')
    source = matches[0]
    path = next((f.get('source_path') for f in job['files'] if f.get('source_hash') == source['content_hash']
                 and f.get('file_name') == source['file_name']), None)
    # Session paths have different display field names in older state rows.
    if not path:
        path = next((f.get('source_path') for f in job['files'] if f.get('source_hash') == source['content_hash']), None)
    if not path or not Path(path).is_file() or hash_file(path) != source['content_hash']:
        raise ValueError('原文件已移动或内容变化，请重新导入后再修正。')
    import fitz
    with fitz.open(path) as document:
        page_count = document.page_count
    action = request.get('action')
    operation = dict(operation_id=operation_id, action=action, binding=_binding(data, source), changes=[])
    if action in {'add', 'restore_region'}:
        fields = dict(request.get('course') or {})
        if action == 'restore_region':
            region = next((r for r in region_choices(snapshot) if r['region_id'] == request.get('region_id')
                           and r['source_hash'] == source['content_hash']), None)
            if not region or region['action'] != 'excluded_from_preview_occupancy':
                raise ValueError('未找到可恢复的自动排除区域。')
            fields.update({k: region[k] for k in ('page', 'week', 'weekday', 'periods')})
            operation['region_id'] = region['region_id']
            operation['region_evidence'] = region
        block = _new_block(data, source, fields, operation_id, page_count)
        if action == 'restore_region':
            for key in ('bbox', 'page_width', 'page_height'):
                if key in region:
                    block[key] = region[key]
            block['provenance']['restored_region'] = region['region_id']
        operation['changes'].append(dict(before=None, after=block))
    elif action in {'delete', 'restore'}:
        pool = data.get('blocks' if action == 'delete' else 'removed_blocks', [])
        block = next((b for b in pool if b['block_id'] == request.get('block_id')
                      and b.get('source_hash') == source['content_hash']), None)
        if not block:
            raise ValueError('这条记录已经变化，请刷新后重试。')
        if action == 'restore' and any(b['block_id'] == block['block_id'] for b in data['blocks']):
            raise ValueError('该课程已恢复。')
        if action == 'restore' and any(b.get('source_hash') == source['content_hash']
                and b.get('week') == block['week'] and b.get('weekday') == block['weekday']
                and _periods(b) & _periods(block) for b in data['blocks']):
            raise ValueError('该时段已有课程，请先处理重复记录再恢复。')
        operation['changes'].append(dict(before=deepcopy(block), after=None if action == 'delete' else deepcopy(block)))
    elif action == 'exclude_region':
        region = next((r for r in region_choices(snapshot) if r['region_id'] == request.get('region_id')
                       and r['source_hash'] == source['content_hash']), None)
        if not region or region['action'] != 'suggested_nonclass_requires_review':
            raise ValueError('未找到可排除的待核对区域。')
        operation.update(region_id=region['region_id'], region_evidence=region)
        for block in data['blocks']:
            if (block.get('source_hash') != source['content_hash'] or block.get('week') != region['week']
                    or block.get('weekday') != region['weekday'] or block.get('page') != region['page']):
                continue
            overlap = _periods(block) & set(region['periods'])
            if not overlap:
                continue
            after = deepcopy(block)
            remaining = sorted(_periods(block) - overlap)
            if remaining:
                after.update(periods=remaining, period=remaining[0])
            else:
                after = None
            operation['changes'].append(dict(before=deepcopy(block), after=after))
        if not operation['changes']:
            raise ValueError('该区域没有可排除的课程占用，请检查当前结果。')
    elif action == 'undo':
        prior = [c['new_value'] for c in snapshot.get('corrections', [])
                 if c['field'] == OPERATION_FIELD and c['source_hash'] == source['content_hash']]
        if not prior or prior[-1]['operation_id'] != request.get('undo_id'):
            raise ValueError('只能撤销这份原件最近的一次课程操作，请刷新后重试。')
        previous = prior[-1]
        for change in previous['changes']:
            after, before = change['after'], change['before']
            block_id = (after or before)['block_id']
            active = next((b for b in data['blocks'] if b['block_id'] == block_id), None)
            # Recalculation may add date/time fields, so compare editable scope.
            keys = ('course', 'week', 'weekday', 'periods', 'source_hash', 'member_key')
            if (active is None) != (after is None) or (active and any(active.get(k) != after.get(k) for k in keys)):
                raise ValueError('该课程已被后续修改，不能覆盖；请修改当前记录。')
            operation['changes'].append(dict(before=deepcopy(active or before), after=deepcopy(before)))
            if before is None:
                operation['changes'][-1]['after'] = None
        operation['undo_of'] = previous['operation_id']
    else:
        raise ValueError('不支持的课程操作。')
    for change in operation['changes']:
        block_id = (change['before'] or change['after'])['block_id']
        change['expected_active'] = _block_state(next((b for b in data['blocks'] if b['block_id'] == block_id), None))
        change['expected_removed'] = _block_state(next((b for b in data.get('removed_blocks', []) if b['block_id'] == block_id), None))
    _apply_operation(snapshot, operation)
    correction = CorrectionRecord(source['content_hash'], operation_id, OPERATION_FIELD,
        [c['before'] for c in operation['changes']], operation, reason, snapshot['parser_signature'],
        str(request.get('operator_id') or '本机用户'), utc_now())
    snapshot.setdefault('corrections', []).append(asdict(correction))
    workflow = restore_workflow(snapshot, schedule_core)
    _refresh_derived(workflow, schedule_core)
    refreshed = snapshot_workflow(workflow)
    with store.connect() as connection:
        # A stale UI must not overwrite a concurrent correction.
        lock_review_revision(connection, job_id, request['revision'])
        store.add_correction(**{k: getattr(correction, k) for k in ('source_hash','block_id','field',
            'original_value','new_value','reason','parser_signature','operator_id')}, job_id=job_id, connection=connection)
        store.save_job_result(job_id, result_json=json.dumps(refreshed, ensure_ascii=False, separators=(',', ':')),
            error=str(job.get('error') or ''), quality_state=refreshed['quality_state'],
            current=int(job.get('current') or 0), total=int(job.get('total') or 0),
            issues=refreshed['issues'], connection=connection)
    return get_review_payload(store, job_id)


def replay_operation(workflow, correction):
    from app.services.workflow_persistence import snapshot_workflow
    operation = correction['new_value']
    if any(c.field == OPERATION_FIELD and c.new_value['operation_id'] == operation['operation_id']
           for c in workflow.process_result.corrections):
        return False
    snapshot = snapshot_workflow(workflow)
    data = snapshot['generation']
    source = next((s for s in data['sources'] if s['content_hash'] == correction['source_hash']), None)
    if source is None:
        return False
    try:
        compatible = _binding(data, source) == operation['binding']
    except ValueError:
        compatible = False
    if compatible and operation.get('region_id'):
        compatible = any(r == operation.get('region_evidence') for r in region_choices(snapshot))
    if compatible:
        for change in operation['changes']:
            block_id = (change['before'] or change['after'])['block_id']
            active = next((b for b in data['blocks'] if b['block_id'] == block_id), None)
            removed = next((b for b in data.get('removed_blocks', []) if b['block_id'] == block_id), None)
            if _block_state(active) != change.get('expected_active') or _block_state(removed) != change.get('expected_removed'):
                compatible = False
                break
    if not compatible:
        workflow.process_result.issues.append(ParseIssue(code='MANUAL_OPERATION_SOURCE_CHANGED',
            message='原件身份、课程记录或排除区域证据已变化，旧课程操作未自动应用，请重新核对。',
            source_hash=source['content_hash'], source_file=source['file_name'], severity='warning', blocks_export=True))
        return False
    _apply_operation(snapshot, operation)
    workflow.generation_result.blocks = snapshot['generation']['blocks']
    workflow.generation_result.removed_blocks = snapshot['generation']['removed_blocks']
    workflow.process_result.issues = [ParseIssue(**i) for i in snapshot['issues']]
    return True
