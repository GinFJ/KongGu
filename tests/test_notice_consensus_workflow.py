from copy import deepcopy
import hashlib

import fitz
import pandas as pd
import pytest

from app.services.desktop_workflow import (
    export_excel, parse_pdf_paths, save_workflow_result, serialize_workflow_result,
)
from app.services.review_service import confirm_issue, get_review_payload
from app.services.state_store import StateStore
from app.services.workflow_persistence import restore_workflow, snapshot_workflow
from core import schedule_core as sc


class NoticeCore:
    def __init__(self, count=1):
        self.count = count
        self.parsed = False
        self.received = []
        self.last_returned = []

    def __getattr__(self, name):
        return getattr(sc, name)

    def infer_pdf_kind(self, file_name, path=''):
        return '英方' if '英方' in file_name else '中方'

    def detect_schedule_layout_profile(self, source, kind):
        return 'test-notice-layout'

    def chinese_image_clipped_pages(self, source):
        return []

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None, progress=None):
        self.parsed = True
        blocks = [dict(name='张三', source_type='中方', kind='中方', week=3,
                       date='2026-03-16', weekday='周一', period=3, periods=[3,4],
                       course='测试课程', source_file=sources[0]['file_name'],
                       text_source='内嵌文本')]
        calendar = pd.DataFrame([dict(date='2026-03-16', week=3, weekday='周一')])
        return blocks, calendar, [], pd.DataFrame(blocks)

    def visual_notice_consensus_evidence(self, source):
        assert self.parsed, 'Evidence is retrieved after the actual parse/cache path.'
        self.received.append(deepcopy(source))
        seeds = [dict(page=0, bbox=[x,50,x+50,70], original_ocr='教研',
                      label='教研', coverage_score=.995) for x in [200,400]]
        self.last_returned = [dict(
            kind='same_page_nonclass_consensus', source_hash=source['content_hash'],
            parser_signature=self.cache_signature(), ocr_signature='test-ocr',
            page=0, page_width=1404, page_height=993, dpi=180,
            bbox=[100+i*80,200,160+i*80,230],
            original_ocr=[dict(text='致研',bbox=[110+i*80,205,140+i*80,225])],
            canonical_label='教研', seeds=deepcopy(seeds),
            match=dict(coverage_score=.99,threshold=.98,method_version='same-page-glyph-v1'),
            action='suggested_nonclass_requires_review', occupancy_changed=False,
            week=3, weekday='周一', periods=[3,4],
        ) for i in range(self.count)]
        return self.last_returned


@pytest.fixture
def notice_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv('KONGGU_SEMESTER_START_DATE', '2026-03-02')
    monkeypatch.setenv('KONGGU_TEACHING_WEEKS', '18')
    path = tmp_path/'张三-中方课表.pdf'
    with fitz.open() as document:
        document.new_page().insert_text((72,72),'Anonymous timetable')
        path.write_bytes(document.tobytes())
    monkeypatch.setattr('app.services.desktop_workflow.inspect_pdf_sources',
        lambda sources: [dict(status='ready',source_file=s.file_name,
                              source_hash=s.content_hash,page_count=1,
                              pages_needing_ocr=[],clipped_pages=[])
                         for s in sources])
    return path


@pytest.mark.parametrize('count', [1,2])
def test_getter_evidence_is_copied_and_each_exclusion_has_a_review_issue(notice_pdf, count):
    core = NoticeCore(count)
    result = parse_pdf_paths(schedule_core=core,paths=[str(notice_pdf)])
    assert len(core.received) == 1
    assert core.received[0]['content_hash'] == hashlib.sha256(notice_pdf.read_bytes()).hexdigest()
    evidence = result.generation_result.pdf_inspections[0]['nonclass_consensus']
    assert evidence == core.last_returned
    core.last_returned[0]['seeds'][0]['bbox'][0] = -1
    assert evidence[0]['seeds'][0]['bbox'][0] == 200
    issues = [i for i in result.process_result.issues if i.code=='VISUAL_NOTICE_CONSENSUS_REVIEW']
    assert len(issues) == count and len({i.issue_id for i in issues}) == count
    assert all(i.severity=='warning' and i.blocks_export and not i.confirmed for i in issues)
    assert all('第1页' in i.message and '第3周' in i.message and '周一' in i.message
               and '致研' in i.message and '教研' in i.message for i in issues)
    assert all('疑似' in i.message and '预览仍保留原占用' in i.message for i in issues)
    assert all('确认此提示只记录核对' in i.suggestion and '不会自动排除' in i.suggestion
               and '保留待复核' in i.suggestion for i in issues)
    assert '保留原占用' in result.generation_result.pdf_inspections[0]['warning']
    assert result.process_result.quality_state == 'needs_review'
    assert len(result.generation_result.course_blocks) == 1
    assert result.generation_result.course_blocks[0].course == '测试课程'


def test_notice_evidence_survives_snapshot_review_and_unconfirmed_export_gate(notice_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr('app.services.desktop_workflow.inspect_pdf_sources',
        lambda sources: [dict(status='ready',source_file=s.file_name,
                              source_hash=s.content_hash,page_count=1,
                              pages_needing_ocr=[1],clipped_pages=[])
                         for s in sources])
    core = NoticeCore()
    result = parse_pdf_paths(schedule_core=core,paths=[str(notice_pdf)])
    original_blocks = deepcopy(result.generation_result.blocks)
    original_occupancy = deepcopy(result.generation_result.occupancy)
    snapshot = snapshot_workflow(result)
    evidence = snapshot['generation']['pdf_inspections'][0]['nonclass_consensus']
    restored = restore_workflow(snapshot, core)
    payload = serialize_workflow_result(restored,'test-result')
    assert payload['can_export'] is False
    assert payload['details'][0]['quality_state'] == 'needs_review'
    assert payload['details'][0]['nonclass_consensus'] == evidence
    target = tmp_path/'unconfirmed.xlsx'
    with pytest.raises(ValueError,match='未确认'):
        export_excel(schedule_core=core,workflow_result=restored,target_path=str(target))
    assert not target.exists()
    store = StateStore(tmp_path/'state.sqlite3')
    job = save_workflow_result(restored,store)
    review = get_review_payload(store,job)
    assert review['inspections'][0]['nonclass_consensus'] == evidence
    issue = next(i for i in review['issues'] if i['code']=='VISUAL_NOTICE_CONSENSUS_REVIEW')
    assert issue['confirmed'] is False
    generic = next(i for i in review['issues'] if i['code']=='CHINESE_IMAGE_PAGE_REVIEW')
    generic_confirmed = confirm_issue(store,job,generic['issue_id'])
    assert generic_confirmed['quality_state'] == 'needs_review'
    assert not next(i for i in generic_confirmed['issues']
                    if i['code']=='VISUAL_NOTICE_CONSENSUS_REVIEW')['confirmed']
    still_pending = restore_workflow(store.get_job(job)['result'],core)
    with pytest.raises(ValueError,match='未确认'):
        export_excel(schedule_core=core,workflow_result=still_pending,target_path=str(target))
    confirmed = confirm_issue(store,job,issue['issue_id'])
    assert confirmed['quality_state'] == 'accepted'
    assert confirmed['inspections'][0]['nonclass_consensus'] == evidence
    final_workflow = restore_workflow(store.get_job(job)['result'],core)
    assert final_workflow.generation_result.blocks == original_blocks
    assert final_workflow.generation_result.occupancy == original_occupancy
    assert len(final_workflow.generation_result.course_blocks) == 1
    assert evidence[0]['action'] == 'suggested_nonclass_requires_review'
    assert evidence[0]['occupancy_changed'] is False


def test_core_without_optional_getter_retains_existing_workflow(notice_pdf):
    core = NoticeCore()
    core.visual_notice_consensus_evidence = None
    result = parse_pdf_paths(schedule_core=core,paths=[str(notice_pdf)])
    assert result.process_result.quality_state == 'accepted'
    assert not any(i.code=='VISUAL_NOTICE_CONSENSUS_REVIEW' for i in result.process_result.issues)


def test_in_memory_evidence_is_retained_without_a_readable_cache(notice_pdf):
    class MemoryOnlyCore(NoticeCore):
        def __init__(self):
            super().__init__()
            self.fresh_cache_reads = 0

        def parse_actual_pdf_sources(self, sources, **kwargs):
            result = super().parse_actual_pdf_sources(sources, **kwargs)
            for source in sources:
                source['_notice_evidence_in_memory'] = True
            return result

        def visual_notice_consensus_evidence(self, source):
            if not source.get('_notice_evidence_in_memory'):
                self.fresh_cache_reads += 1
                return []
            return super().visual_notice_consensus_evidence(source)

    core = MemoryOnlyCore()
    result = parse_pdf_paths(schedule_core=core,paths=[str(notice_pdf)])
    assert core.fresh_cache_reads == 0
    assert len(result.generation_result.pdf_inspections[0]['nonclass_consensus']) == 1
    assert any(i.code=='VISUAL_NOTICE_CONSENSUS_REVIEW' for i in result.process_result.issues)
    assert result.process_result.quality_state == 'needs_review'
