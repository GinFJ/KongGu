import json
from dataclasses import asdict

import fitz
import pytest
from openpyxl import load_workbook

from app.services.desktop_workflow import export_excel, load_workflow_result
from app.services.state_store import StateStore
from core import schedule_core as sc
from core.models import CorrectionRecord, ParseIssue
from core.legacy_adapter import hash_file
from core.signature import build_parser_signature
from tests.course_verification_helpers import verify_test_job


@pytest.fixture
def departments(tmp_path):
    signature = build_parser_signature().digest
    students = ['成员甲｜办公室｜干事', '成员甲｜宣传部｜干事', '成员乙｜宣传部｜干事']
    blocks, sources, paths = [], [], []
    for member_index, key in enumerate(students):
        name, department, role = key.split('｜')
        for kind, period in [('中方', member_index + 1), ('英方', member_index + 5)]:
            path = tmp_path / f'{department}-{name}-{role}-{kind}.pdf'
            with fitz.open() as pdf:
                pdf.new_page().insert_text((45, 50), f'Synthetic fixture {len(paths)}')
                pdf.save(path)
            digest = hash_file(path)
            paths.append(str(path))
            sources.append(dict(file_name=path.name, kind=kind, content_hash=digest, source_path=''))
            blocks.append(dict(member_key=key, name=name, department=department, role=role,
                week=1, weekday='周一', period=period, periods=[period],
                course=f'{department}课程', source_type=kind, kind=kind, source=kind,
                source_file=path.name, source_hash=digest, block_id=f'b-{len(blocks)}'))
    blocks.append({**blocks[0], 'week':2, 'course':'仅第2周课程', 'block_id':'week-two'})
    calendar = [{'date': '2026-03-02', 'weekday':'周一', 'week':1},
                {'date': '2026-03-09', 'weekday':'周一', 'week':2}]
    snapshot = {'version':1, 'generation':dict(sources=sources, blocks=blocks, students=students, weeks=[1,2],
        calendar_rows=calendar, occupancy=[dict(week=w, weekday=d, period=p, members=sorted(v))
        for (w,d,p),v in sc.build_occupancy(blocks).items()]), 'quality_state':'accepted',
        'issues':[asdict(ParseIssue('CHECKED_SOURCE', '对照宣传部和办公室原件', source_hash=sources[0]['content_hash'],
                 severity='info', confirmed=True, blocks_export=False))],
        'corrections':[asdict(CorrectionRecord(sources[0]['content_hash'], 'b-0', 'course',
            '跨部旧记录', '办公室课程', '参见宣传部原始备注', signature))], 'parser_signature':signature}
    store = StateStore(tmp_path/'store.sqlite3')
    job = store.create_job({}, signature, paths)
    for path, source in zip(paths, sources):
        store.update_file(job, path, source_hash=source['content_hash'])
    store.save_job_result(job,result_json=json.dumps(snapshot, ensure_ascii=False),error='',
                          quality_state='accepted',current=len(paths),total=len(paths),issues=snapshot['issues'])
    return store,job


def _all_values(book):
    return '\n'.join(str(c) for sheet in book for row in sheet.values for c in row if c is not None)


def test_department_classic_workbook_scopes_every_sheet_and_recomputes_namesakes(departments,tmp_path):
    store,job = departments
    verify_test_job(store,job)
    workflow = load_workflow_result(job,store,sc)
    target = export_excel(schedule_core=sc,workflow_result=workflow,target_path=str(tmp_path/'department.xlsx'),
                          department='办公室',week=1)
    book = load_workbook(target)
    try:
        values = _all_values(book)
        assert all(token not in values for token in ('宣传部','成员乙','跨部旧记录','参见','仅第2周课程'))
        assert '办公室课程' in values and 'CHECKED_SOURCE' in values
        assert '第2周' not in book.sheetnames
        roster = list(book['成员完整性检查'].values)
        assert roster[1:] == [('成员甲', '已参与统计')]
        rows = list(book['空课明细'].values)
        on_monday = {r['节次']:r for r in [dict(zip(rows[0],row)) for row in rows[1:]] if r['星期']=='周一'}
        assert on_monday[1]['有课人数']==1 and on_monday[1]['空闲人数']==0
        assert on_monday[2]['空闲人数']==1 and on_monday[2]['空闲人员']=='成员甲'
        assert on_monday[2]['有课人数']==0
        assert book['文件处理记录'].max_row==3
        assert book['人工修正历史'].max_row==2
        for label in ('课程占用明细', '成员课程明细', '空课明细', '全部空课明细'):
            data = list(book[label].values)
            index = data[0].index('周次')
            assert {r[index] for r in data[1:]} == {1}
    finally:
        book.close()
    full = export_excel(schedule_core=sc,workflow_result=workflow,target_path=str(tmp_path/'full.xlsx'))
    book = load_workbook(full)
    try:
        values = _all_values(book)
        assert '参见宣传部原始备注' in values and '仅第2周课程' in values and '第2周' in book.sheetnames
    finally:
        book.close()


def test_qinghe_workdays_week_and_batch_gate(departments,tmp_path):
    store,job = departments
    target = tmp_path/'template.xlsx'
    with pytest.raises(ValueError, match='人工核验'):
        export_excel(schedule_core=sc,workflow_result=load_workflow_result(job,store,sc),
                     target_path=str(target),mode='qinghe',department='办公室',week=1)
    assert not target.exists()
    verify_test_job(store,job)
    workflow = load_workflow_result(job,store,sc)
    for invalid in ({'department':'无此部门'}, {'week':99}, {'week':True}):
        with pytest.raises(ValueError):
            export_excel(schedule_core=sc,workflow_result=workflow,target_path=str(target),mode='qinghe',**invalid)
    export_excel(schedule_core=sc,workflow_result=workflow,target_path=str(target),mode='qinghe',
                 department='办公室',week=1)
    book = load_workbook(target)
    try:
        assert book.sheetnames==['第1周']
        sheet=book.active
        assert sheet['A1'].value=='办公室 · 第一周空课表'
        assert sheet['R3'].value=='周五' and 'A1:U2' in sheet.merged_cells
        assert sheet['A33'].value=='中午' and sheet['A75'].value=='9-11节'
        assert sheet['B5'].value is None and sheet['F5'].value=='成员甲'
        assert '宣传部' not in _all_values(book)
    finally:
        book.close()
