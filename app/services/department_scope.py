"""Select a department by the reviewed member identities, including namesakes."""
from dataclasses import replace

import pandas as pd

from app.services.review_operations import OPERATION_FIELD, OPERATION_LABELS


def department_members(workflow, department):
    matched = {m.member_key for m in workflow.process_result.members
               if (m.department or '').strip() == department}
    members = [key for key in workflow.generation_result.students if key in matched]
    if not department or not members:
        raise ValueError('当前批次没有这个部门的成员，请重新选择部门。')
    return members


def department_occupancy(occupancy, members):
    allowed = set(members)
    return {slot: occupied.intersection(allowed) for slot, occupied in occupancy.items()}


def _department_slots(frame, weeks, occupancy, members):
    # Project exactly the slots which exist in this batch, preserving its calendar
    # dates and clocks. An absent slot must not turn into a new all-free period.
    if not {'week', 'weekday', 'period'}.issubset(frame.columns):
        raise ValueError('当前批次缺少空课明细，请重新生成后导出。')
    slots = frame.loc[frame['week'].isin(weeks)].copy()
    if slots.empty:
        raise ValueError('所选周次没有可导出的空课明细。')
    occupied_rows = [occupancy.get((int(r.week), r.weekday, int(r.period)), set())
                     for r in slots.itertuples(index=False)]
    slots['occupied_members'] = ['、'.join(sorted(row)) for row in occupied_rows]
    slots['occupied_count'] = [len(row) for row in occupied_rows]
    slots['free_members'] = ['、'.join(key for key in members if key not in row) for row in occupied_rows]
    slots['free_count'] = [len(members) - len(row) for row in occupied_rows]
    return slots


def department_workbook_request(request, workflow, schedule_core, department):
    generation = workflow.generation_result
    process = workflow.process_result
    members = department_members(workflow, department)
    allowed = set(members)
    occupancy = department_occupancy(generation.occupancy, allowed)
    slots = _department_slots(generation.all_slot_df, request.weeks, occupancy, members)

    # Keep source-specific diagnostic records only for sources owned wholly by
    # the selected department. Removed blocks still establish source ownership.
    owners = {}
    for block in generation.blocks + generation.removed_blocks:
        owners.setdefault(block.get('source_hash'), set()).add(block.get('member_key') or block.get('name'))
    selected_files = [f for f in process.file_records if f.source.content_hash and
                      f.source.content_hash in owners and owners[f.source.content_hash] <= allowed]
    labels = {f.source.content_hash: f'{f.source.kind}课表 · {f.source.content_hash[:8]}' for f in selected_files}
    file_hashes = {}
    for f in process.file_records:
        file_hashes.setdefault(f.source.file_name, set()).add(f.source.content_hash)
    block_hashes = {}
    for block in generation.blocks + generation.removed_blocks:
        block_hashes.setdefault(block.get('block_id'), set()).add(block.get('source_hash'))
    scoped_blocks = []
    for block in generation.blocks:
        if (block.get('member_key') or block.get('name')) in allowed and block.get('week') in request.weeks:
            kind = block.get('source_type') or block.get('kind') or ''
            scoped_blocks.append({**block, 'source': kind, 'source_file': labels.get(block.get('source_hash'), kind)})
    block_df = schedule_core.blocks_to_dataframe(scoped_blocks)
    # A note or prior identity can name another department. The department
    # workbook uses structured audit fields; full audit text stays in the full export.
    file_df = pd.DataFrame([{'来源': labels[f.source.content_hash], '成员': '、'.join(sorted(
        key.split('｜')[0] for key in owners[f.source.content_hash])), '类型': f.source.kind,
        '质量状态': f.quality_state, '全批来源课程块数': f.block_count} for f in selected_files])
    issue_rows = []
    for issue in process.issues:
        if issue.source_hash:
            candidates = {issue.source_hash}
        elif issue.source_file:
            candidates = file_hashes.get(issue.source_file, set())
        else:
            candidates = block_hashes.get(issue.block_id, set()) if issue.block_id else set()
        if len(candidates) == 1:
            label = labels.get(next(iter(candidates)))
            if label:
                issue_rows.append({'来源': label, '问题ID': issue.issue_id, '错误码': issue.code,
                                   '级别': issue.severity, '已确认': '是' if issue.confirmed else '否'})
    correction_df = pd.DataFrame([{'来源': labels[c.source_hash],
        '字段': OPERATION_LABELS.get(c.new_value.get('action'), '课程操作') if c.field == OPERATION_FIELD else c.field,
        '时间': c.created_at, '是否过期': '是' if c.stale else '否'}
        for c in process.corrections if c.source_hash in labels])
    notes = pd.concat([request.instructions_df, pd.DataFrame([
        {'项目': '导出部门', '说明': department},
        {'项目': '部门复核记录', '说明': '按本部门独占来源汇总问题类型与修正字段；跨部门事项及自由文本留存在全协会工作簿。'},
    ])], ignore_index=True)
    return replace(request, students=members, occupancy=occupancy, blocks_df=block_df, member_course_df=block_df,
                   all_slot_df=slots, issue_df=pd.DataFrame(issue_rows),
                   correction_df=correction_df, file_df=file_df, instructions_df=notes)
