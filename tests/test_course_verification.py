from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.services.course_verification import review_blocks, verification_queue, verify_course
from app.services.desktop_workflow import export_excel, load_workflow_result
from app.services.job_service import JobCoordinator
from app.services.review_service import apply_correction, get_review_payload
from app.services.state_store import StateStore
from core import schedule_core as sc
from tests.test_review_operations import saved


def test_missing_page_is_only_recovered_from_the_same_single_page_pdf():
    inspections = [dict(source_hash='single', status='ready', page_count=1),
                   dict(source_hash='multi', status='ready', page_count=2)]
    raw = [dict(source_hash=k, bbox=[10, 10, 20, 20]) for k in ('single', 'multi', 'absent')]
    assert [r.get('page') for r in review_blocks(raw, inspections)] == [0, None, None]
    assert all('page' not in r for r in raw)


def test_native_points_and_known_raster_scale_can_be_displayed():
    row = dict(bbox=[2, 4, 6, 8], page=0)
    valid = [{**row, 'text_source': '内嵌文本'}, {**row, 'page_width': 100, 'page_height': 200}]
    assert all(r.get('bbox') == row['bbox'] for r in review_blocks(valid, []))
    assert 'bbox' not in review_blocks([{**row, 'text_source': 'OCR'}], [])[0]


def test_only_group_identical_printed_cells():
    row = dict(source_hash='a', source_file='test.pdf', page=0, bbox=[2, 4, 6, 8],
               member_key='m', course='Sample', weekday='周一', periods=[1, 2], week=1, block_id='one')
    rows = [row, {**row, 'week': 3, 'block_id': 'three'}]
    for index, change in enumerate([dict(source_hash='b'), dict(page=1), dict(bbox=[2,4,7,8]),
                                    dict(member_key='n'), dict(course='Other'), dict(weekday='周二'),
                                    dict(periods=[2]), dict(bbox=None), dict(bbox=None)]):
        rows.append({**row, **change, 'block_id': str(index)})
    result = verification_queue(rows, 'signature', set())
    assert result['total'] == 10
    first = result['items'][0]
    assert first['weeks'] == [1, 3] and first['block_ids'] == ['one', 'three']
    checked = {first['item_id']}
    assert verification_queue(list(reversed(rows)), 'signature', checked)['confirmed'] == 1
    assert verification_queue(rows, 'new-signature', checked)['confirmed'] == 0
    changed = deepcopy(rows)
    changed[1]['week'] += 1
    assert verification_queue(changed, 'signature', checked)['confirmed'] == 0


def test_one_printed_cell_combines_expanded_periods_but_keeps_different_weeks_separate():
    row = dict(source_hash='a', source_file='source.pdf', page=0, bbox=[2, 4, 6, 8],
               member_key='m', course='Sample', weekday='周一')
    blocks = [{**row, 'week': week, 'periods': [p], 'block_id': f'{week}-{p}'}
              for week, period_list in [(1, [2,3]), (2, [2,3]), (3, [2])] for p in period_list]
    original = deepcopy(blocks)
    first, second = verification_queue(blocks, 's', set())['items']
    assert (first['weeks'], first['periods']) == ([1,2], [2,3])
    assert (second['weeks'], second['periods']) == ([3], [2])
    assert set(first['block_ids'] + second['block_ids']) == {r['block_id'] for r in blocks}
    assert blocks == original


def test_course_reviews_persist_and_gate_normal_job_and_export_paths(saved, tmp_path):
    store, job, _, _ = saved
    coordinator = JobCoordinator(sc, store)
    first = get_review_payload(store, job)
    items = first['verification']['items']
    assert len(items) == 2 and first['verification']['confirmed'] == 0
    assert not coordinator.get(job)['job']['output']['can_export']
    target = tmp_path / 'confirmed.xlsx'
    with pytest.raises(ValueError, match='人工核验'):
        export_excel(schedule_core=sc, workflow_result=load_workflow_result(job, store, sc),
                     target_path=str(target))
    assert not target.exists()
    receipt = verify_course(store, job, items[0]['item_id'], first['revision'], compact=True)
    one = get_review_payload(store, job)
    assert receipt["previous_revision"] == first["revision"] and receipt["revision"] == one["revision"]
    assert receipt["verification"]["confirmed"] == 1 and "blocks" not in receipt
    # Retrying with an old revision returns a complete authoritative view.
    assert verify_course(store, job, items[0]['item_id'], first['revision'], compact=True)["blocks"]
    assert one['verification']['pending'] == 1
    assert verify_course(store, job, items[0]['item_id'], first['revision'])['revision'] == one['revision']
    with pytest.raises(ValueError, match='已变化'):
        verify_course(store, job, items[1]['item_id'], first['revision'])
    verify_course(store, job, items[1]['item_id'], one['revision'])
    assert coordinator.get(job)['job']['output']['can_export']
    reopened = StateStore(store.path)
    assert get_review_payload(reopened, job)['verification']['confirmed'] == 2
    export_excel(schedule_core=sc, workflow_result=load_workflow_result(job, reopened, sc),
                 target_path=str(target))
    revised = apply_correction(store=store, schedule_core=sc, job_id=job, block_id=items[0]['block_ids'][0],
                               field='course', new_value='Updated', reason='Checked PDF', operator_id='operator')
    assert not next(i for i in revised['verification']['items']
                    if i['block_ids'] == items[0]['block_ids'])['confirmed']
    assert next(i for i in revised['verification']['items']
                if i['block_ids'] == items[1]['block_ids'])['confirmed']
    assert not coordinator.get(job)['job']['output']['can_export']


def test_refuses_changed_original_and_unknown_item(saved):
    store, job, paths, _ = saved
    initial = get_review_payload(store, job)
    Path(paths[0]).write_bytes(b'changed')
    with pytest.raises(ValueError, match='原件'):
        verify_course(store, job, initial['verification']['items'][0]['item_id'], initial['revision'])
    with pytest.raises(ValueError, match='已变化'):
        verify_course(store, job, 'unknown', initial['revision'])
    assert get_review_payload(store, job)['verification']['confirmed'] == 0


def test_racing_confirmations_cannot_overwrite_each_other(saved):
    store, job, _, _ = saved
    first = get_review_payload(store, job)
    barrier = Barrier(2)

    def confirm(item):
        barrier.wait(timeout=10)
        try:
            verify_course(store, job, item['item_id'], first['revision'])
            return True
        except ValueError as exc:
            assert '已变化' in str(exc)
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(confirm, first['verification']['items'])) == [False, True]
    fresh = get_review_payload(store, job)
    remaining = next(i for i in fresh['verification']['items'] if not i['confirmed'])
    assert verify_course(store, job, remaining['item_id'], fresh['revision'])['verification']['pending'] == 0
