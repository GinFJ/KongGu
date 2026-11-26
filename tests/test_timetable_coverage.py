from datetime import date,timedelta

import fitz
import pandas as pd
import pytest

from core import schedule_core as sc
from core.timetable_coverage import english_grid_coverage,covered_weeks


def calendar():
    return pd.DataFrame([dict(week=day//7+1,date=(date(2026,3,2)+timedelta(days=day)).isoformat(),
                             weekday=sc.WEEKDAYS[day%7]) for day in range(28)])


def source_pdf():
    with fitz.open() as doc:
        page=doc.new_page()
        page.insert_text((40,40),'Timetable for Anonymous')
        for index,day in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday']):
            page.insert_text((40,80+index*50),day)
            page.insert_text((40,92+index*50),f'2026-03-{16+index:02}')
        return dict(bytes=doc.tobytes(),kind='英方',file_name='测试-英方课表.pdf')


def test_blank_dated_rows_establish_coverage_but_missing_weeks_do_not(monkeypatch):
    source=source_pdf()
    def unexpected(*a,**k):raise AssertionError('Readable text must not trigger OCR')
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',unexpected)
    coverage=english_grid_coverage(source,sc,allow_ocr=False)
    assert len(coverage['dates'])==5
    inspections=[dict(source_hash=sc._source_digest(source),date_coverage=coverage)]
    assert covered_weeks(inspections,calendar(),[1,2,3,4])==[3]


def test_coverage_intersects_sources_and_requires_a_complete_workweek():
    dates=[f'2026-03-{n:02}' for n in range(16,21)]
    records=[dict(source_hash='a',date_coverage=dict(source_hash='a',dates=dates)),
             dict(source_hash='b',date_coverage=dict(source_hash='b',dates=dates[:-1]))]
    assert covered_weeks(records,calendar(),[1,2,3,4])==[]
    records[1]['date_coverage']['dates']=dates
    assert covered_weeks(records,calendar(),[1,2,3,4])==[3]
    records[1]['source_hash']='changed'
    with pytest.raises(ValueError,match='来源不一致'):
        covered_weeks(records,calendar(),[3])


@pytest.mark.parametrize('start,end',[('2026-03-16','2026-03-18'),('20260316','2026-0318')])
def test_query_end_does_not_turn_later_printed_blank_rows_into_free_dates(start,end):
    source=source_pdf()
    with fitz.open(stream=source['bytes'],filetype='pdf') as d:
        d[0].insert_text((250,45),'Start:')
        d[0].insert_text((400,45),'End:')
        d[0].insert_text((250,60),start)
        d[0].insert_text((400,60),end)
        source['bytes']=d.tobytes()
    coverage=english_grid_coverage(source,sc,allow_ocr=False)
    assert coverage['dates']==['2026-03-16','2026-03-17','2026-03-18']
    assert covered_weeks([dict(source_hash=coverage['source_hash'],date_coverage=coverage)],calendar(),[3])==[]


def test_page_break_date_is_covered_without_matching_the_prior_pages_weekday():
    source=source_pdf()
    with fitz.open(stream=source['bytes'],filetype='pdf') as d:
        d[0].add_redact_annot(fitz.Rect(35,62,120,79.5),fill=(1,1,1))
        d[0].apply_redactions()  # readable Monday date remains; its label was on the prior page
        assert '2026-03-16' in d[0].get_text()
        source['bytes']=d.tobytes()
    coverage=english_grid_coverage(source,sc,allow_ocr=False)
    assert len(coverage['dates'])==5


def test_restored_review_and_export_cannot_expand_source_coverage(tmp_path,monkeypatch):
    from tests.test_desktop_workflow import FakeDesktopCore
    from app.services.desktop_workflow import parse_pdf_paths,export_excel
    from app.services.workflow_persistence import restore_workflow,snapshot_workflow
    from app.services.review_service import _refresh_derived
    class Core(FakeDesktopCore):
        def __getattr__(self,key):return getattr(sc,key)
        def detect_schedule_layout_profile(self,*a,**k):return 'synthetic'
        def parse_actual_pdf_sources(self,*args,**kwargs):
            blocks,_,errors,preview=super().parse_actual_pdf_sources(*args,**kwargs)
            blocks.append({**blocks[0], 'kind':'英方', 'source_type':'英方', 'source':'英方',
                           'source_file':args[0][1]['file_name'], 'period':3, 'periods':[3]})
            return blocks,calendar(),errors,preview
    source=source_pdf()
    path=tmp_path/'办公室-张三-干事-英方课表.pdf';path.write_bytes(source['bytes'])
    chinese=tmp_path/'办公室-张三-干事-中方课表.pdf'
    with fitz.open() as d:
        d.new_page().insert_text((40,40),'Anonymous other schedule')
        chinese.write_bytes(d.tobytes())
    monkeypatch.setattr('app.services.desktop_workflow.inspect_pdf_sources',
        lambda sources:[dict(status='ready',source_hash=s.content_hash,file_name=s.file_name,
                              page_count=1,pages_needing_ocr=[],clipped_pages=[]) for s in sources])
    core=Core()
    result=parse_pdf_paths(schedule_core=core,paths=[str(chinese),str(path)])
    assert result.generation_result.weeks==[3]
    assert any(i.code=='TIMETABLE_COVERAGE_LIMIT' for i in result.process_result.issues)
    from tests.course_verification_helpers import verify_test_workflow
    restored=restore_workflow(snapshot_workflow(result),core)
    _refresh_derived(restored,core)
    # Supply the original files to this new synthetic review session.
    for original, source in zip((chinese, path), restored.generation_result.model_sources):
        source.source_path = str(original)
    restored = verify_test_workflow(restored, core, tmp_path)
    assert restored.generation_result.weeks==[3]
    export_excel(schedule_core=core,workflow_result=restored,target_path=str(tmp_path/'result.xlsx'),export_week_count=20)
    assert core.export_kwargs['weeks']==[3]
