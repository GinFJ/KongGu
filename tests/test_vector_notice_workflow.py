from app.services.desktop_workflow import parse_pdf_paths, serialize_workflow_result
from app.services.review_service import confirm_issue, get_review_payload
from app.services.desktop_workflow import save_workflow_result
from app.services.state_store import StateStore
from tests.test_notice_consensus_workflow import NoticeCore, notice_pdf


def test_applied_vector_notice_is_explicit_and_requires_its_own_review(notice_pdf,tmp_path):
    class VectorCore(NoticeCore):
        def visual_notice_consensus_evidence(self,source):
            notes=super().visual_notice_consensus_evidence(source)
            for note in notes:
                note['action']='excluded_from_preview_occupancy'
                note['occupancy_changed']=True
                note['match']['method_version']='same-page-vector-glyph-v1'
            return notes
    core=VectorCore()
    workflow=parse_pdf_paths(schedule_core=core,paths=[str(notice_pdf)])
    issue=next(i for i in workflow.process_result.issues if i.code=='VISUAL_NOTICE_CONSENSUS_REVIEW')
    assert '预览已按非课程排除' in issue.message
    assert '若判断有误' in issue.suggestion and '保留待复核' in issue.suggestion
    assert issue.blocks_export and not issue.confirmed
    assert serialize_workflow_result(workflow,'result')['can_export'] is False
    store=StateStore(tmp_path/'state.sqlite3')
    job=save_workflow_result(workflow,store)
    note=get_review_payload(store,job)['inspections'][0]['nonclass_consensus'][0]
    assert note['occupancy_changed'] and note['action']=='excluded_from_preview_occupancy'
    assert confirm_issue(store,job,issue.issue_id)['quality_state']=='accepted'
