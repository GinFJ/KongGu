"""A close vector match remains a review candidate, including real stroke edits."""
from copy import deepcopy

import fitz
import numpy as np
import pytest

from app.services.desktop_workflow import (
    export_excel, parse_pdf_paths, save_workflow_result, serialize_workflow_result,
)
from app.services.review_service import confirm_issue, get_review_payload
from app.services.state_store import StateStore
from app.services.workflow_persistence import restore_workflow
from core.vector_notice_consensus import match_vector_notices
from tests.test_notice_consensus_workflow import NoticeCore, notice_pdf


def _attached_stroke_pdf():
    """The third word has a 2 x 1 pt attached bar within the same contour."""
    regions = []
    with fitz.open() as document:
        page = document.new_page(width=120, height=210)
        for index, y in enumerate([20, 80, 140]):
            for x in [35, 52]:
                points = [(x, y), (x+11, y), (x+11, y+4), (x+5, y+4)]
                if index == 2:
                    points += [(x+5, y+8), (x+7, y+8),
                               (x+7, y+9), (x+5, y+9)]
                points += [(x+5, y+15), (x, y+15)]
                shape = page.new_shape()
                shape.draw_polyline(points)
                shape.finish(color=None, fill=(0,), closePath=True)
                shape.commit()
            regions.append(dict(
                bbox=tuple(v*2.5 for v in (10, y-8, 110, y+30)),
                content_bbox=tuple(v*2.5 for v in (13, y-5, 107, y+27)),
                page=0, texts=['教研' if index < 2 else '未知'],
                has_course_code=False, ink=np.zeros((2, 2), dtype='uint8'),
            ))
        return document.tobytes(), regions


def test_high_coverage_attached_stroke_candidate_requires_specific_review_before_export(
    notice_pdf, tmp_path, monkeypatch,
):
    data, regions = _attached_stroke_pdf()
    notice_pdf.write_bytes(data)
    with fitz.open(stream=data, filetype='pdf') as document:
        matches = match_vector_notices(document[0], regions)
    # This is an observed classifier boundary: changed geometry can still
    # satisfy coverage and topology. A score is not semantic verification.
    assert len(matches) == 1
    match = matches[0]
    assert all(.98 <= score < 1 for score in match['scores'])
    assert match['requires_review']

    class AttachedStrokeCore(NoticeCore):
        def visual_notice_consensus_evidence(self, source):
            note = super().visual_notice_consensus_evidence(source)[0]
            note.update(
                bbox=list(match['bbox']), canonical_label=match['label'],
                original_ocr=[dict(text='未知', bbox=list(match['bbox']))],
                seeds=[dict(page=0, bbox=list(box), original_ocr='教研',
                            label='教研', coverage_score=score)
                       for box, score in zip(match['support_bboxes'], match['scores'])],
                match=dict(coverage_score=min(match['scores']), threshold=.98,
                           method_version='same-page-vector-glyph-v1'),
                vector_evidence=deepcopy(match['vector_evidence']),
                action='excluded_from_preview_occupancy', occupancy_changed=True,
                requires_review=True,
            )
            return [note]

    monkeypatch.setattr('app.services.desktop_workflow.inspect_pdf_sources',
        lambda sources: [dict(status='ready', source_file=s.file_name,
                              source_hash=s.content_hash, page_count=1,
                              pages_needing_ocr=[1], clipped_pages=[])
                         for s in sources])
    core = AttachedStrokeCore()
    workflow = parse_pdf_paths(schedule_core=core, paths=[str(notice_pdf)])
    assert workflow.process_result.quality_state == 'needs_review'
    assert serialize_workflow_result(workflow, 'result')['can_export'] is False
    store = StateStore(tmp_path/'state.sqlite3')
    job = save_workflow_result(workflow, store)
    issues = get_review_payload(store, job)['issues']
    specific = next(i for i in issues if i['code'] == 'VISUAL_NOTICE_CONSENSUS_REVIEW')
    assert specific['blocks_export'] and not specific['confirmed']
    generic = next(i for i in issues if i['code'] == 'CHINESE_IMAGE_PAGE_REVIEW')
    assert confirm_issue(store, job, generic['issue_id'])['quality_state'] == 'needs_review'
    restored = restore_workflow(store.get_job(job)['result'], core)
    assert serialize_workflow_result(restored, 'result')['can_export'] is False
    target = tmp_path/'unchecked-stroke-candidate.xlsx'
    with pytest.raises(ValueError, match='未确认'):
        export_excel(schedule_core=core, workflow_result=restored, target_path=str(target))
    assert not target.exists()
    assert not next(i for i in get_review_payload(store, job)['issues']
                    if i['issue_id'] == specific['issue_id'])['confirmed']
