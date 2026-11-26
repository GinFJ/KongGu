"""End-to-end review of a dated batch using two anonymous PDFs and SQLite."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from shutil import copyfile
from dataclasses import asdict

import pytest
from openpyxl import load_workbook

from app.services.date_review import apply_date_scope, date_bounds, update_date_review
from app.services.calendar_settings import save_calendar_settings
from app.services.desktop_workflow import export_excel, load_workflow_result, serialize_workflow_result
from app.services.job_service import JobCoordinator
from app.services.review_service import confirm_issue, get_review_payload
from app.services.state_store import StateStore
from app.sidecar import dispatch
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from core import schedule_core as sc
from core.models import ParseIssue
from tests.test_review_operations import saved, save
from tests.test_e2e_workflow_real_core import _chinese_pdf, _english_pdf, _isolate_caches, _pair


@pytest.fixture
def batch(saved):
    store, job, _, member = saved
    snapshot = store.get_job(job)['result']
    data = snapshot['generation']
    data['calendar_rows'] = sc.synthesize_calendar_from_blocks([]).to_dict('records')
    data['blocks'][0].update(periods=[1, 2])
    en = data['sources'][1]['content_hash']
    data['layout_profiles'] = {data['sources'][0]['content_hash']: 'cdut_undergrad_full_term_cn'}
    data['pdf_inspections'] = [dict(source_hash=en, status='ready', page_count=1,
                                     date_coverage=dict(source_hash=en, dates=['2026-09-07']))]
    snapshot['date_review'] = dict(start_date='2026-09-07', end_date='2026-09-08', log=[], confirmed={})
    saved_flow = restore_workflow(snapshot, sc)
    save(store, job, snapshot_workflow(saved_flow))
    return store, job, member


def action(batch, name, **kwargs):
    store, job, key = batch
    request = dict(operation_id=str(uuid4()), revision=get_review_payload(store, job)['revision'],
                   member_key=key, **kwargs)
    return update_date_review(store, sc, job, request, name)


def test_direct_slot_edit_reversible_overlap_date_scope_and_export(batch, tmp_path):
    store, job, member = batch
    initial = get_review_payload(store, job)
    cn = initial['sources'][0]['content_hash']
    assert initial['date_review']['members'][0]['missing_dates'] == ['2026-09-08']
    output = lambda: serialize_workflow_result(load_workflow_result(job, store, sc), job)
    assert not output()['availability_preview']
    target = tmp_path / 'range.xlsx'
    with pytest.raises(ValueError, match='冲突'):
        export_excel(schedule_core=sc, workflow_result=load_workflow_result(job, store, sc), target_path=target)
    slot = dict(source_hash=cn, date='2026-09-07', period=2, page=0)
    off = action(batch, 'slot', **slot, marked=False)
    assert next(r for r in off['blocks'] if r['source_hash'] == cn)['periods'] == [1]
    assert load_workflow_result(job, store, sc).generation_result.occupancy[1,'周一',2] == {member}
    on = action(batch, 'slot', **slot, marked=True)
    assert next(r for r in on['blocks'] if r['source_hash'] == cn)['periods'] == [1, 2]
    with pytest.raises(ValueError, match='缺资料'):
        action(batch, 'member', acknowledge_missing=False)
    action(batch, 'slot', source_hash='manual', date='2026-09-08', period=3, marked=True, page=0)
    # Explicitly reviewing a legitimate overlap is independent of filling a gap.
    with pytest.raises(ValueError, match='冲突'):
        action(batch, 'member', acknowledge_missing=True)
    conflict = next(i for i in get_review_payload(store, job)['issues'] if i['code'] == 'SCHEDULE_CONFLICT')
    confirm_issue(store, job, conflict['issue_id'])
    confirmed = action(batch, 'member', acknowledge_missing=True)
    assert confirmed['date_review']['members'][0]['confirmed']
    assert len(confirmed['date_review']['log']) == 4
    assert {r['日期'] for r in output()['availability_preview']} == {'2026-09-07', '2026-09-08'}
    restored = load_workflow_result(job, StateStore(store.path), sc)
    assert serialize_workflow_result(restored, job)['can_export']
    assert restored.generation_result.occupancy[1,'周二',3] == {member}
    export_excel(schedule_core=sc, workflow_result=restored, target_path=target)
    with target.open('rb') as stream:
        workbook = load_workbook(stream, read_only=True)
        assert workbook.sheetnames
        workbook.close()
    action(batch, 'slot', **slot, marked=False)
    assert not output()['can_export']


def test_range_rejects_stale_click_and_changes_outside_selected_days(batch):
    store, job, key = batch
    initial = get_review_payload(store, job)
    request = dict(source_hash=initial['sources'][0]['content_hash'], member_key=key,
        date='2026-09-07', period=3, page=0, marked=True, revision=initial['revision'], operation_id=str(uuid4()))
    first = update_date_review(store, sc, job, request, 'slot')
    assert update_date_review(store, sc, job, request, 'slot')['revision'] == first['revision']
    with pytest.raises(ValueError, match='变化'):
        update_date_review(store, sc, job, {**request,'operation_id':str(uuid4())}, 'slot')
    with pytest.raises(ValueError, match='范围'):
        action(batch, 'slot', source_hash='manual', date='2026-09-09', period=3, marked=True, page=0)
    assert len(get_review_payload(store, job)['date_review']['log']) == 1


def test_date_bounds_and_incomplete_calendar(batch):
    with pytest.raises(ValueError, match='结束'):
        date_bounds(dict(start_date='2026-09-08', end_date='2026-09-07'))
    store, job, _ = batch
    wf = load_workflow_result(job, store, sc)
    wf.date_review['end_date'] = '2028-09-09'
    with pytest.raises(ValueError, match='校历'):
        apply_date_scope(wf, sc)
    coordinator = JobCoordinator(sc, store)
    with pytest.raises(ValueError, match='校历'):
        coordinator.start(dict(paths=['never-opened.pdf'], start_date='2028-09-09', end_date='2028-09-12'))
    assert not coordinator.has_active_job()


def test_job_creates_one_range_for_two_members_and_restores_progress(tmp_path, monkeypatch):
    _isolate_caches(monkeypatch, tmp_path)
    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE', '2026-03-02')
    paths = list(_pair(tmp_path))
    other = tmp_path / '宣传部-李四-干事-中方课表.pdf'
    _chinese_pdf(other)
    paths.append(other)
    store = StateStore(tmp_path / 'jobs.sqlite3')
    coordinator = JobCoordinator(sc, store)
    job = coordinator.start(dict(paths=list(map(str, paths)), start_date='2026-03-05', end_date='2026-03-09'),
                            background=False)['job_id']
    entry = coordinator.get(job)['job']
    assert entry['status'] == 'completed', entry.get('error')
    view = get_review_payload(store, job)['date_review']
    assert len(view['members']) == 2
    assert len(view['calendar']) == 5
    assert not entry['output']['availability']
    for i, person in enumerate(view['members']):
        action((store, job, person['member_key']), 'member', acknowledge_missing=True)
        output = coordinator.get(job)['job']['output']
        assert output['can_export'] == (i == 1)
        assert bool(output['availability_preview']) == (i == 1)
    assert {row['日期'] for row in output['availability_preview']} == {day['date'] for day in view['calendar']}


def test_same_name_identical_bytes_different_departments_never_export_one_member(tmp_path, monkeypatch):
    _isolate_caches(monkeypatch, tmp_path)
    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE', '2026-03-02')
    paths = list(_pair(tmp_path))
    for source in paths.copy():
        duplicate = source.with_name(source.name.replace('活动部', '宣传部'))
        copyfile(source, duplicate)
        paths.append(duplicate)
    store = StateStore(tmp_path / 'duplicates.sqlite3')
    dates = dict(start_date='2026-03-30', end_date='2026-04-03')
    job = JobCoordinator(sc, store).start(dict(paths=list(map(str, paths)), **dates), background=False)['job_id']
    review = get_review_payload(store, job)
    issues = [i for i in review['issues'] if i['code'] == 'INPUT_PREFLIGHT_FAILED']
    assert len(issues) == 2
    for source, duplicate in zip(paths[:2], paths[2:]):
        assert any(source.name in i['message'] and duplicate.name in i['message'] for i in issues)
    target = tmp_path/'unsafe.xlsx'
    with pytest.raises(ValueError, match='成员不一致'):
        export_excel(schedule_core=sc, workflow_result=load_workflow_result(job,store,sc), target_path=target)
    # Recreate the faulty 0.13.0 persisted record, including its old approval.
    snap = store.get_job(job)['result']
    from app.services.date_review import review_members
    person, = review_members(snap['date_review'], snap['generation'])
    snap['date_review']['confirmed'][person['member_key']] = person['fingerprint']
    snap['issues'] = []
    snap['quality_state'] = 'accepted'
    save(store,job,snap)
    reopened = StateStore(store.path)
    repaired = get_review_payload(reopened,job)
    assert {i['issue_id'] for i in repaired['issues']} == {i['issue_id'] for i in issues}
    with pytest.raises(ValueError, match='成员不一致'):
        export_excel(schedule_core=sc,workflow_result=load_workflow_result(job,reopened,sc),target_path=target)
    assert not target.exists()
    # Two independently sourced PDFs can safely have identical member names.
    # Replace the fixtures' bytes while retaining both declared departments.
    for path, writer in zip(paths[2:], (_chinese_pdf, _english_pdf)):
        path.unlink()
        writer(path)
    coordinator = JobCoordinator(sc,reopened)
    job = coordinator.start(dict(paths=list(map(str,paths)), **dates), background=False)['job_id']
    members = get_review_payload(reopened,job)['date_review']['members']
    assert {(m['name'],m['department']) for m in members} == {('张三','活动部'),('张三','宣传部')}
    for member in members:
        action((reopened,job,member['member_key']), 'member', acknowledge_missing=True)
    good = tmp_path/'两个部门.xlsx'
    export_excel(schedule_core=sc,workflow_result=load_workflow_result(job,reopened,sc),target_path=good)
    workbook = load_workbook(good,read_only=True)
    try:
        roster = list(workbook['成员完整性检查'].values)
        assert len(roster) == 3 and all(any('张三' in str(value) for value in row) for row in roster[1:])
    finally:
        workbook.close()


def test_conflicts_are_limited_to_owner_and_dates_across_months_and_restart(tmp_path, monkeypatch):
    _isolate_caches(monkeypatch, tmp_path)
    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE','2026-03-02')
    chinese, english = _pair(tmp_path)
    english.unlink()
    _english_pdf(english,[('Monday','2026-03-02','1','Physics'),('Tuesday','2026-03-03','2','Chemistry'),
        ('Wednesday','2026-03-04','3','Biology'),('Thursday','2026-03-05','4','History'),
        ('Monday','2026-03-09','5','Literature'),('Tuesday','2026-03-10','6','Geography'),
        ('Wednesday','2026-03-11','7','Economics'),('Thursday','2026-03-12','8','Philosophy')])
    other = tmp_path/'宣传部-李四-干事-中方课表.pdf'
    _chinese_pdf(other)
    paths = [str(p) for p in (chinese, english, other)]
    store = StateStore(tmp_path/'conflict.sqlite3')
    coordinator = JobCoordinator(sc,store)
    def start(first, last):
        return coordinator.start(dict(paths=paths,start_date=first,end_date=last),background=False)['job_id']
    job = start('2026-03-02','2026-03-03')
    view = get_review_payload(store,job)
    conflict, = [i for i in view['issues'] if i['code']=='SCHEDULE_CONFLICT']
    assert conflict['source_hash'] and conflict['block_id'] and '2026-03-02 第1节' in conflict['message']
    members = {m['name']:m['member_key'] for m in view['date_review']['members']}
    action((store,job,members['李四']), 'member', acknowledge_missing=True)
    with pytest.raises(ValueError,match='冲突'):
        action((store,job,members['张三']), 'member', acknowledge_missing=True)
    confirm_issue(store,job,conflict['issue_id'])
    action((store,job,members['张三']), 'member', acknowledge_missing=True)
    assert coordinator.get(job)['job']['output']['can_export']
    # An exact 0.13.0 human-confirmed global warning can be attributed to one slot.
    snap = store.get_job(job)['result']
    member = next(m for m in load_workflow_result(job,store,sc).process_result.members if m.name=='张三')
    legacy = asdict(ParseIssue(code='SCHEDULE_CONFLICT',message=member.display_remark,field='periods',
                               blocks_export=True,confirmed=True))
    snap['issues'] = [legacy]
    save(store,job,snap)
    reopened = StateStore(store.path)
    same, = get_review_payload(reopened,job)['issues']
    assert same['issue_id'] == conflict['issue_id'] and same['confirmed']
    assert JobCoordinator(sc,reopened).get(job)['job']['output']['can_export']
    job = start('2026-03-30','2026-04-03')
    snap = store.get_job(job)['result']
    assert all(i['code']!='SCHEDULE_CONFLICT' for i in snap['issues'])
    snap['issues'].append({**legacy,'confirmed':False})
    snap['quality_state'] = 'needs_review'
    save(store,job,snap)
    paths_to_bind = store.source_paths_for_job(job)
    reopened = StateStore(store.path)
    reopened.bind_session_paths(job,paths_to_bind)
    assert all(i['code']!='SCHEDULE_CONFLICT' for i in get_review_payload(reopened,job)['issues'])
    for key in members.values():
        action((reopened,job,key),'member',acknowledge_missing=True)
    target = tmp_path/'跨月.xlsx'
    export_excel(schedule_core=sc, workflow_result=load_workflow_result(job,reopened,sc),target_path=target)
    book = load_workbook(target,read_only=True)
    try:
        rows = book['空课明细'].values
        header = next(rows)
        assert {str(row[header.index('日期')])[:10] for row in rows} == {
            '2026-03-30','2026-03-31','2026-04-01','2026-04-02','2026-04-03'}
    finally:
        book.close()


def test_concurrent_clicks_one_wins_and_department_has_correct_free_members(batch, tmp_path):
    store, job, key = batch
    review = get_review_payload(store, job)
    hashes = [s['content_hash'] for s in review['sources']]
    sync = Barrier(2)
    def click(period):
        request = dict(operation_id=str(uuid4()), revision=review['revision'], member_key=key,
                       source_hash=hashes[0], date='2026-09-07', period=period, marked=False, page=0)
        sync.wait(timeout=10)
        try:
            update_date_review(store, sc, job, request, 'slot')
        except ValueError as exc:
            assert '变化' in str(exc)
            return None
        return period
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcome = list(pool.map(click, [1, 2]))
    winner, = [p for p in outcome if p is not None]
    latest = get_review_payload(store, job)
    assert len(latest['date_review']['log']) == 1
    # Finish removing both CN slots. At period 2 the EN source still occupies it.
    action(batch, 'slot', source_hash=hashes[0], date='2026-09-07', period=3-winner, marked=False, page=0)
    action(batch, 'member', acknowledge_missing=True)
    restored = load_workflow_result(job, store, sc)
    result = serialize_workflow_result(restored, job)
    dept = next(d for d in result['department_scopes'] if d['department'] == '办公室')
    for period, busy in [(1, False), (2, True)]:
        index = next(i for i,r in enumerate(result['availability_preview']) if r['日期'] == '2026-09-07' and r['节次'] == period)
        assert bool(dept['busy'][index]) == busy
        assert result['availability_preview'][index]['空闲人数'] == int(not busy)
    for department in ('', '办公室'):
        path = tmp_path / f'范围{department}.xlsx'
        export_excel(schedule_core=sc, workflow_result=restored, target_path=path, department=department)
        book = load_workbook(path, read_only=True)
        try:
            rows = book['空课明细'].values
            header = next(rows)
            detail = [dict(zip(header, row)) for row in rows]
            first, second = next(r for r in detail if r['星期']=='周一' and r['节次']==1), next(r for r in detail if r['星期']=='周一' and r['节次']==2)
            assert first['空闲人数'] == 1 and second['空闲人数'] == 0
        finally:
            book.close()


def test_flag_reopens_member_persists_without_editing_occupancy_and_resolves(batch, tmp_path):
    store, job, person = batch
    conflict, = get_review_payload(store, job)['issues']
    confirm_issue(store, job, conflict['issue_id'])
    action(batch, 'member', acknowledge_missing=True)
    review = get_review_payload(store, job)
    source = review['sources'][0]['content_hash']
    target = dict(source_hash=source, date='2026-09-08', period=3, page=0)
    original_blocks = review['blocks']
    request = dict(command='review.flag', job_id=job, revision=review['revision'],
        operation_id=str(uuid4()), member_key=person, open=True, bbox=[10,15,45,60],
        page_width=200, page_height=300, **target)
    account_paths = SimpleNamespace(app_data_root=tmp_path/'isolated-app')
    save_calendar_settings(account_paths, dict(semester_start_date='2026-09-07', teaching_weeks=18))
    flagged = dispatch(request, JobCoordinator(sc, store), account_paths=account_paths)
    assert flagged['blocks'] == original_blocks
    assert not flagged['date_review']['members'][0]['confirmed']
    assert flagged['date_review']['flags'] == [flagged['date_review']['log'][-1]]
    assert dispatch(request, JobCoordinator(sc, store), account_paths=account_paths)['revision'] == flagged['revision']
    paths = store.source_paths_for_job(job)
    reopened = StateStore(store.path)
    assert get_review_payload(reopened, job)['date_review']['flags'][0]['bbox'] == [10,15,45,60]
    reopened.bind_session_paths(job, paths)
    current = (reopened, job, person)
    with pytest.raises(ValueError, match='待核实'):
        action(current, 'member', acknowledge_missing=True)
    with pytest.raises(ValueError, match='待核对'):
        export_excel(schedule_core=sc, workflow_result=load_workflow_result(job,reopened,sc),
                     target_path=tmp_path/'not-ready.xlsx')
    # Closing the flag explicitly retains the original empty slot.
    done = action(current, 'flag', open=False, **target)
    assert done['blocks'] == original_blocks and not done['date_review']['flags']
    assert not done['date_review']['members'][0]['confirmed']
    action(current, 'member', acknowledge_missing=True)
    export_excel(schedule_core=sc, workflow_result=load_workflow_result(job,reopened,sc),
                 target_path=tmp_path/'closed.xlsx')
    book = load_workbook(tmp_path/'closed.xlsx', read_only=True)
    try:
        values = {cell for row in book['人工修正历史'].values for cell in row}
        assert {'问题已核实','标记待核实'} <= values
    finally:
        book.close()


def test_flag_checks_original_grid_and_partial_removal_preserves_position(batch):
    store, job, key = batch
    snap = store.get_job(job)['result']
    snap['generation']['blocks'][0].update(bbox=[10,15,45,60],page_width=200,page_height=300)
    save(store,job,snap)
    cn = snap['generation']['sources'][0]['content_hash']
    cell = dict(source_hash=cn,date='2026-09-07',period=2,page=0)
    for change in ({'source_hash':'manual'},{'date':'2026-09-09'},{'period':999},
                   {'source_hash':'unknown'},{'page':3},{'bbox':[-1,0,50,60],'page_width':100,'page_height':100}):
        with pytest.raises(ValueError):
            action(batch,'flag',open=True,**{**cell,**change})
    assert not get_review_payload(store,job)['date_review']['flags']
    action(batch,'flag',open=True,**cell)
    off = action(batch,'slot',marked=False,**cell)
    mark, = off['date_review']['removed_positions']
    assert mark['periods'] == [2] and mark['date'] == cell['date'] and mark['bbox'] == [10,15,45,60]
    assert off['date_review']['flags']
    action(batch,'flag',open=False,**cell)
    restored = action(batch,'slot',marked=True,**cell)
    assert not restored['date_review']['removed_positions']
