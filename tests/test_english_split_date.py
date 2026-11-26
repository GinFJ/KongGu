"""Real PDF page breaks must not silently lose a dated course row."""
import fitz
import pytest

from core import schedule_core as sc


def split_date_pdf(*, conflicting_weekday=False, missing_date=False):
    doc = fitz.open()
    first = doc.new_page(width=800, height=400)
    columns = [100 + n * 50 for n in range(11)]
    for period, center in enumerate(columns, 1):
        first.insert_text((center-3, 345), str(period), fontsize=8)
    first.insert_text((10, 390), 'Tuesday' if conflicting_weekday else 'Monday', fontsize=8)
    for start, end, label in [(75,175,'ALPHA'),(175,275,'BETA')]:
        first.draw_rect(fitz.Rect(start,360,end,400),color=(0,0,0),fill=(.8,.8,.8))
        first.insert_text((start+15,380),label,fontsize=8)
    second = doc.new_page(width=800,height=400)
    if not missing_date:
        second.insert_text((10,8),'2026-05-25',fontsize=8)
    for weekday, date, y in [('Tuesday','2026-05-26',50),('Wednesday','2026-05-27',100),('Thursday','2026-05-28',150)]:
        second.insert_text((10,y),weekday,fontsize=8)
        second.insert_text((10,y+10),date,fontsize=8)
    data=doc.tobytes()
    doc.close()
    return {'bytes':data,'kind':'英方','file_name':'测试-英方课表.pdf'}


def test_weekday_and_date_on_adjacent_pdf_pages_preserve_merged_courses():
    source=split_date_pdf()
    blocks=sc._parse_english_pdf_grid_layout(source,'测试-英方课表.pdf')
    assert {(b['date'],b['period'],b['course']) for b in blocks} == {
        ('2026-05-25',1,'ALPHA'),('2026-05-25',2,'ALPHA'),
        ('2026-05-25',3,'BETA'),('2026-05-25',4,'BETA'),
    }


@pytest.mark.parametrize('options',[{'conflicting_weekday':True},{'missing_date':True}])
def test_unresolved_page_edge_is_flagged_instead_of_disappearing(options):
    source=split_date_pdf(**options)
    blocks=sc._parse_english_pdf_grid_layout(source,'测试-英方课表.pdf')
    assert not blocks
    assert any(n['kind']=='unresolved_date_row' for n in source.get('_english_grid_evidence',[]))
