"""Simulate individual confirmations for synthetic PDFs in export tests."""
from app.services.course_verification import verify_course
from app.services.desktop_workflow import load_workflow_result, save_workflow_result
from app.services.review_service import get_review_payload
from app.services.state_store import StateStore


def verify_test_job(store, job_id):
    review = get_review_payload(store, job_id)
    for item in review['verification']['items']:
        if not item['confirmed']:
            review = verify_course(store, job_id, item['item_id'], review['revision'])
    return review


def verify_test_workflow(workflow, core, tmp_path):
    store = StateStore(tmp_path / 'visual-check.sqlite3')
    job_id = save_workflow_result(workflow, store)
    verify_test_job(store, job_id)
    return load_workflow_result(job_id, store, core)
