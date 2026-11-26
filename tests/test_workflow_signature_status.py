"""Export readiness follows the active parser without rewriting past quality."""
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.desktop_workflow import (
    DesktopWorkflowResult, export_excel, serialize_workflow_result,
)
from core.models import ProcessResult
from core.signature import build_parser_signature


def _workflow(signature, quality='accepted'):
    return DesktopWorkflowResult(
        generation_result=SimpleNamespace(blocks=[], pdf_inspections=[]),
        process_result=ProcessResult(parser_signature=signature, quality_state=quality),
        preview_df=pd.DataFrame(), warnings=['已有处理提示'], errors=[],
    )


@pytest.mark.parametrize('quality,can_export', [
    ('accepted', True), ('needs_review', False), ('blocked', False),
])
def test_current_signature_preserves_quality_and_export_readiness(quality, can_export):
    workflow = _workflow(build_parser_signature().digest, quality)
    payload = serialize_workflow_result(workflow, 'result')
    assert payload['can_export'] is can_export
    assert payload['quality_state'] == quality
    assert payload['warnings'] == ['已有处理提示']


@pytest.mark.parametrize('signature', ['old-parser-signature', ''])
def test_old_or_missing_signature_disables_export_with_actionable_warning(signature):
    workflow = _workflow(signature)
    payload = serialize_workflow_result(workflow, 'result')
    assert payload['can_export'] is False
    assert payload['quality_state'] == 'accepted'
    assert payload['parser_signature'] == signature
    assert any('解析规则' in warning and '重新解析' in warning for warning in payload['warnings'])
    assert workflow.warnings == ['已有处理提示']
    assert workflow.process_result.quality_state == 'accepted'


def test_repeated_status_reads_do_not_accumulate_warning_or_mutate_snapshot():
    workflow = _workflow('old-parser-signature')
    first = serialize_workflow_result(workflow, 'result')
    second = serialize_workflow_result(workflow, 'result')
    assert first['warnings'] == second['warnings']
    assert len(second['warnings']) == 2
    assert second['summary']['warning_count'] == 2
    assert workflow.warnings == ['已有处理提示']


def test_actual_export_still_rejects_old_signature_before_writing(tmp_path):
    target = tmp_path/'must-not-exist.xlsx'
    with pytest.raises(ValueError, match='解析规则已变化'):
        export_excel(schedule_core=object(), workflow_result=_workflow('old-parser-signature'),
                     target_path=str(target))
    assert not target.exists()


def test_job_poll_cache_tracks_live_signature_without_losing_same_signature_cache(tmp_path, monkeypatch):
    import app.services.job_service as jobs
    from app.services.desktop_workflow import parse_pdf_paths, save_workflow_result
    from app.services.state_store import StateStore
    from tests.test_desktop_workflow import FakeDesktopCore, _minimal_pdf_bytes

    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE', '2026-03-02')
    monkeypatch.setenv('KONGGU_TEACHING_WEEKS', '18')
    pdf = tmp_path/'张三-中方课表.pdf'
    pdf.write_bytes(_minimal_pdf_bytes())
    core = FakeDesktopCore()
    workflow = parse_pdf_paths(schedule_core=core, paths=[str(pdf)])
    store = StateStore(tmp_path/'state.sqlite3')
    job_id = save_workflow_result(workflow, store)
    from tests.course_verification_helpers import verify_test_job
    verify_test_job(store, job_id)
    saved = store.get_job(job_id)['result']
    coordinator = jobs.JobCoordinator(core, store)
    restores = []
    original_restore = jobs.restore_workflow

    def counted_restore(snapshot, schedule_core):
        restores.append(snapshot)
        return original_restore(snapshot, schedule_core)

    monkeypatch.setattr(jobs, 'restore_workflow', counted_restore)
    assert coordinator.get(job_id)['job']['output']['can_export'] is True
    assert coordinator.get(job_id)['job']['output']['can_export'] is True
    assert len(restores) == 1

    monkeypatch.setenv('KONGGU_TEACHING_WEEKS', '19')
    changed = coordinator.get(job_id)['job']['output']
    assert changed['can_export'] is False
    assert changed['quality_state'] == 'accepted'
    assert any('重新解析' in warning for warning in changed['warnings'])
    assert coordinator.get(job_id)['job']['output']['can_export'] is False
    assert len(restores) == 2

    # Returning to the original configuration revalidates that same snapshot;
    # no confirmation, course data or historical quality is rewritten.
    monkeypatch.setenv('KONGGU_TEACHING_WEEKS', '18')
    restored = coordinator.get(job_id)['job']['output']
    assert restored['can_export'] is True
    assert not any('重新解析' in warning for warning in restored['warnings'])
    assert coordinator.get(job_id)['job']['output']['can_export'] is True
    assert len(restores) == 3
    assert store.get_job(job_id)['result'] == saved
