"""A fresh weekly import must reuse only unchanged, evidenced review decisions."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from app.services.review_service import confirm_issue
from app.services.review_evidence import reuse_confirmed_issues
from app.services.state_store import StateStore
from core.models import ParseIssue
from tests.test_review_correction_consistency import _saved_job


def save(store, snapshot, job_id=None):
    job_id = job_id or store.create_job({}, snapshot['parser_signature'], [])
    store.save_job_result(job_id, result_json=json.dumps(snapshot), error='',
                          quality_state='needs_review', current=2, total=2, issues=snapshot['issues'])
    return job_id


def reviewed(tmp_path):
    store, job, _, _ = _saved_job(tmp_path)
    snapshot = store.get_job(job)['result']
    snapshot['issues'] = [asdict(ParseIssue(code='ENGLISH_IMAGE_PAGE_REVIEW', message='核对课程格',
        severity='warning', source_hash='hash-en', source_file='成员甲-英方课表.pdf', blocks_export=True))]
    save(store, snapshot, job)
    confirm_issue(store, job, snapshot['issues'][0]['issue_id'])
    return store, snapshot, job


def test_fresh_import_after_restart_reuses_confirmed_warning(tmp_path):
    store, snapshot, _ = reviewed(tmp_path)
    store = StateStore(store.path)
    snapshot['generation']['sources'].reverse()  # selection order is immaterial
    snapshot['generation']['blocks'].reverse()
    job = save(store, snapshot)
    assert reuse_confirmed_issues(store, job) == 1
    assert store.get_job(job)['quality_state'] == 'accepted'
    assert store.get_job(job)['issues'][0]['confirmed']
    assert reuse_confirmed_issues(store, job) == 0


@pytest.mark.parametrize('change', ['hash', 'name', 'course', 'periods', 'calendar', 'signature', 'issue', 'missing_source'])
def test_changed_evidence_requires_review(tmp_path, change):
    store, snapshot, _ = reviewed(tmp_path)
    gen = snapshot['generation']
    if change == 'hash': gen['sources'][0]['content_hash'] = 'different'
    elif change == 'name': gen['blocks'][0]['member_key'] = '成员乙｜办公室｜干事'
    elif change == 'course': gen['blocks'][0]['course'] = '另一门课'
    elif change == 'periods': gen['blocks'][0]['periods'] = [3]
    elif change == 'calendar': gen['calendar_rows'][0]['date'] = '2026-09-08'
    elif change == 'signature': snapshot['parser_signature'] = 'new-parser'
    elif change == 'issue': snapshot['issues'][0]['message'] = '另一项异常'
    else: gen['sources'].pop()
    job = save(store, snapshot)
    assert reuse_confirmed_issues(store, job) == 0
    assert not store.get_job(job)['issues'][0]['confirmed']


def test_editing_old_snapshot_cannot_rewrite_confirmation_evidence(tmp_path):
    store, snapshot, old = reviewed(tmp_path)
    snapshot['generation']['blocks'][0]['course'] = '后改的课程'
    altered_old = deepcopy(snapshot)
    altered_old['issues'][0]['confirmed'] = True
    save(store, altered_old, old)
    assert reuse_confirmed_issues(store, save(store, snapshot)) == 0


def test_error_never_reused_and_deleting_history_removes_evidence(tmp_path):
    store, snapshot, old = reviewed(tmp_path)
    blocked = deepcopy(snapshot)
    blocked['issues'][0]['severity'] = 'error'
    assert reuse_confirmed_issues(store, save(store, blocked)) == 0
    store.clear_job_data(old)
    assert reuse_confirmed_issues(store, save(store, snapshot)) == 0


def test_job_poll_cannot_finish_before_review_reuse(tmp_path, monkeypatch):
    """Poll at the exact persist/reuse boundary seen by the installed UI."""
    import app.services.job_service as jobs
    import app.services.review_evidence as evidence
    from app.services.workflow_persistence import restore_workflow
    from core import schedule_core

    store, snapshot, previous = reviewed(tmp_path)
    paths = store.source_paths_for_job(previous)
    workflow = restore_workflow(snapshot, schedule_core)
    monkeypatch.setattr(jobs, 'parse_pdf_paths', lambda **kwargs: workflow)
    coordinator = jobs.JobCoordinator(schedule_core, store)
    polled = []

    def reuse_after_poll(current_store, job_id):
        polled.append(coordinator.get(job_id)['job'])
        return reuse_confirmed_issues(current_store, job_id)

    monkeypatch.setattr(evidence, 'reuse_confirmed_issues', reuse_after_poll)
    started = coordinator.start({'paths': paths}, background=False)
    assert len(polled) == 1
    assert polled[0]['status'] == 'running'
    result = store.get_job(started['job_id'])
    assert result['status'] == 'completed'
    assert result['quality_state'] == 'accepted'
    assert result['issues'][0]['confirmed']
