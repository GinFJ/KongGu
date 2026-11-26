"""Exercise the persisted review -> recalculate -> export path, including denial."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

import fitz
import openpyxl
import pytest

from app.services.review_operations import mutate_courses
from app.services.review_service import get_review_payload, apply_saved_corrections
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from app.services.desktop_workflow import export_excel, _notice_consensus_issue
from core import schedule_core as sc
from core.legacy_adapter import hash_file
from core.signature import build_parser_signature
from tests.test_review_correction_consistency import _saved_job
from tests.course_verification_helpers import verify_test_job


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE', '2026-09-07')
    monkeypatch.setenv('KONGGU_TEACHING_WEEKS', '18')
    store, job, paths, member = _saved_job(tmp_path)
    snapshot = store.get_job(job)['result']
    for index, path in enumerate(paths):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((70, 70), f'Anonymous timetable {index}')
            pdf.save(path)
        digest = hash_file(path)
        snapshot['generation']['sources'][index]['content_hash'] = digest
        snapshot['generation']['blocks'][index].update(source_hash=digest, page=0)
        store.update_file(job, path, source_hash=digest)
    snapshot['parser_signature'] = build_parser_signature().digest
    save(store, job, snapshot)
    return store, job, paths, member


def save(store, job, snapshot):
    store.save_job_result(job, result_json=json.dumps(snapshot, ensure_ascii=False),
        error='', quality_state=snapshot['quality_state'], current=2, total=2, issues=snapshot['issues'])


def mutate(saved, action, **kwargs):
    store, job, _, _ = saved
    review = get_review_payload(store, job)
    request = dict(action=action, source_hash=review['sources'][0]['content_hash'],
                   reason='对照原件修正', operation_id=str(uuid4()), revision=review['revision'], **kwargs)
    return mutate_courses(store=store, schedule_core=sc, job_id=job, request=request)


def workflow(saved):
    return restore_workflow(saved[0].get_job(saved[1])['result'], sc)


def test_delete_last_source_course_keeps_member_and_pair_and_restores(saved, tmp_path):
    removed = mutate(saved, 'delete', block_id='block-0')
    assert len(removed['removed_blocks']) == 1
    verify_test_job(saved[0], saved[1])
    result = workflow(saved)
    assert result.generation_result.students == [saved[3]]
    assert (1, '周一', 1) not in result.generation_result.occupancy
    assert result.process_result.members[0].has_chinese
    assert result.process_result.quality_state == 'accepted'
    target = tmp_path/'reviewed.xlsx'
    export_excel(schedule_core=sc, workflow_result=result, target_path=str(target))
    book = openpyxl.load_workbook(target)
    assert '人工修正历史' in book.sheetnames
    history = list(book['人工修正历史'].values)
    assert any('对照原件修正' in str(row) and '删除误识别记录' in str(row) for row in history)
    restored = mutate(saved, 'restore', block_id='block-0')
    assert not restored['removed_blocks']
    assert workflow(saved).generation_result.occupancy[(1, '周一', 1)] == {saved[3]}


def test_add_duplicate_guard_replay_and_signature_invalidation(saved):
    original = deepcopy(saved[0].get_job(saved[1])['result'])
    added = mutate(saved, 'add', course=dict(course='漏识别课程', week=1, weekday='周一', periods=[3,4], page=0))
    block = next(b for b in added['blocks'] if b['course'] == '漏识别课程')
    assert block['page'] == 0 and block['source_hash'] == added['sources'][0]['content_hash']
    assert workflow(saved).generation_result.occupancy[(1, '周一', 4)] == {saved[3]}
    with pytest.raises(ValueError, match='已有课程'):
        mutate(saved, 'add', course=dict(course='重复', week=1, weekday='周一', periods=[4], page=0))
    fresh = restore_workflow(original, sc)
    assert apply_saved_corrections(fresh, saved[0], sc) == 1
    assert fresh.generation_result.occupancy == workflow(saved).generation_result.occupancy
    original['parser_signature'] = 'changed-parser'
    stale = restore_workflow(original, sc)
    assert apply_saved_corrections(stale, saved[0], sc) == 0


def test_added_multi_period_course_exports_every_period_and_clock(saved, tmp_path):
    mutate(saved, 'add', course=dict(course='连续午间课程', week=1, weekday='周一', periods=[12,13], page=0))
    target = tmp_path/'multi-period.xlsx'
    verify_test_job(saved[0], saved[1])
    export_excel(schedule_core=sc, workflow_result=workflow(saved), target_path=str(target))
    book = openpyxl.load_workbook(target)
    for name in ('课程占用明细', '成员课程明细'):
        rows = list(book[name].values)
        records = [dict(zip(rows[0], row)) for row in rows[1:]]
        actual = {(row['节次'], row['时间']) for row in records if row['课程'] == '连续午间课程'}
        assert actual == {(p, sc.period_time_range(p)) for p in (12,13)}
    book.close()


def region(saved, action, periods=(3,4)):
    store, job, _, _ = saved
    snapshot = store.get_job(job)['result']
    source = snapshot['generation']['sources'][0]
    note = dict(kind='same_page_nonclass_consensus', source_hash=source['content_hash'], page=0,
        page_width=600, page_height=800, bbox=[20,30,100,90], original_ocr=['未知'],
        canonical_label='教研', action=action, week=1, weekday='周一', periods=list(periods))
    issue = _notice_consensus_issue(source['file_name'], source['content_hash'], note)
    snapshot['generation']['pdf_inspections'] = [dict(source_file=source['file_name'],
        source_hash=source['content_hash'], page_count=1, nonclass_consensus=[note])]
    snapshot['issues'] = [asdict(issue)]
    snapshot['quality_state'] = 'needs_review'
    save(store, job, snapshot)
    return issue.issue_id


def test_restore_auto_excluded_region_uses_evidence_scope_and_resolves_only_its_issue(saved):
    original = deepcopy(saved[0].get_job(saved[1])['result'])
    region_id = region(saved, 'excluded_from_preview_occupancy')
    result = mutate(saved, 'restore_region', region_id=region_id, course=dict(course='原件上的真实课程'))
    assert result['quality_state'] == 'accepted'
    block = next(b for b in result['blocks'] if b['course'] == '原件上的真实课程')
    assert block['periods'] == [3,4] and block['bbox'] == [20,30,100,90]
    assert workflow(saved).generation_result.occupancy[(1,'周一',3)] == {saved[3]}
    assert result['corrections'][0]['new_value']['region_evidence']['bbox'] == block['bbox']
    fresh = restore_workflow(original, sc)
    assert apply_saved_corrections(fresh, saved[0], sc) == 0
    assert fresh.process_result.quality_state == 'needs_review'
    assert (1,'周一',3) not in fresh.generation_result.occupancy


def test_exclude_suggested_region_is_an_actual_removal(saved):
    region_id = region(saved, 'suggested_nonclass_requires_review', (1,))
    result = mutate(saved, 'exclude_region', region_id=region_id)
    assert result['quality_state'] == 'accepted'
    assert (1, '周一', 1) not in workflow(saved).generation_result.occupancy
    mutate(saved, 'restore', block_id='block-0')
    assert workflow(saved).generation_result.occupancy[(1, '周一', 1)] == {saved[3]}
    assert workflow(saved).process_result.quality_state == 'needs_review'


def test_idempotency_stale_revision_and_atomic_save(saved, monkeypatch):
    store, job, _, _ = saved
    review = get_review_payload(store, job)
    request = dict(action='delete', source_hash=review['sources'][0]['content_hash'], block_id='block-0',
                   reason='误识别', operation_id=str(uuid4()), revision=review['revision'])
    first = mutate_courses(store=store, schedule_core=sc, job_id=job, request=request)
    again = mutate_courses(store=store, schedule_core=sc, job_id=job, request=request)
    assert len(first['corrections']) == len(again['corrections']) == 1
    with pytest.raises(ValueError, match='结果已经变化'):
        mutate_courses(store=store, schedule_core=sc, job_id=job, request={**request,'operation_id':str(uuid4())})
    before = deepcopy(store.get_job(job)['result'])
    monkeypatch.setattr(store, 'save_job_result', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk failure')))
    with pytest.raises(RuntimeError, match='disk failure'):
        mutate(saved, 'restore', block_id='block-0')
    assert store.get_job(job)['result'] == before
    assert len(get_review_payload(store, job)['corrections']) == 1


def test_bad_source_gate_remains_and_changed_pdf_cannot_be_edited(saved):
    store, job, paths, _ = saved
    snap = store.get_job(job)['result']
    snap['issues'] = [asdict(__import__('core.models',fromlist=['ParseIssue']).ParseIssue(
        code='CHINESE_IMAGE_CLIPPED', message='页面裁切', severity='error', blocks_export=True))]
    save(store, job, snap)
    assert mutate(saved, 'delete', block_id='block-0')['quality_state'] == 'blocked'
    Path(paths[0]).write_bytes(b'changed-source')
    with pytest.raises(ValueError, match='原文件已移动或内容变化'):
        mutate(saved, 'restore', block_id='block-0')


def test_zero_active_courses_keeps_complete_member_and_slots(saved):
    mutate(saved, 'delete', block_id='block-0')
    store, job, _, member = saved
    review = get_review_payload(store, job)
    request = dict(action='delete', source_hash=review['sources'][1]['content_hash'], block_id='block-1',
                   reason='整份核对无课程', operation_id=str(uuid4()), revision=review['revision'])
    mutate_courses(store=store, schedule_core=sc, job_id=job, request=request)
    result = workflow(saved)
    assert result.generation_result.students == [member]
    assert result.process_result.quality_state == 'accepted'
    assert result.process_result.members[0].is_complete
    assert result.process_result.slots
    assert all(slot.free_members == [member.split('｜')[0]] for slot in result.process_result.slots)


def test_renamed_source_does_not_reuse_removal(saved):
    original = deepcopy(saved[0].get_job(saved[1])['result'])
    mutate(saved, 'delete', block_id='block-0')
    original['generation']['sources'][0]['file_name'] = '另一成员-中方课表.pdf'
    fresh = restore_workflow(original, sc)
    assert apply_saved_corrections(fresh, saved[0], sc) == 0
    assert len(fresh.generation_result.blocks) == 2
    assert any(i.code == 'MANUAL_OPERATION_SOURCE_CHANGED' for i in fresh.process_result.issues)
    assert fresh.process_result.quality_state != 'accepted'


def test_partial_region_exclusion_and_undo_survive_reimport(saved):
    store, job, _, member = saved
    snapshot = store.get_job(job)['result']
    snapshot['generation']['blocks'][0].update(periods=[1,3], period=1)
    save(store, job, snapshot)
    region_id = region(saved, 'suggested_nonclass_requires_review', (3,))
    original = deepcopy(store.get_job(job)['result'])
    after = mutate(saved, 'exclude_region', region_id=region_id)
    assert next(b for b in after['blocks'] if b['block_id']=='block-0')['periods'] == [1]
    undo_id = after['applied_operations'][-1]['new_value']['operation_id']
    restored = mutate(saved, 'undo', undo_id=undo_id)
    assert next(b for b in restored['blocks'] if b['block_id']=='block-0')['periods'] == [1,3]
    assert restored['quality_state'] == 'needs_review'
    fresh = restore_workflow(original, sc)
    assert apply_saved_corrections(fresh, store, sc) == 2
    assert fresh.generation_result.occupancy[(1,'周一',3)] == {member}


@pytest.mark.parametrize('fields', [dict(page=9),dict(week=30),dict(periods=[]),dict(periods=[14]),dict(course='')])
def test_invalid_add_never_changes_state(saved, fields):
    before = deepcopy(saved[0].get_job(saved[1])['result'])
    course = dict(course='漏课', page=0, week=1, weekday='周一', periods=[3])
    course.update(fields)
    with pytest.raises(ValueError):
        mutate(saved,'add',course=course)
    assert saved[0].get_job(saved[1])['result'] == before
