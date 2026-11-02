import cv2
import numpy as np
import pytest

from core import schedule_core as sc


@pytest.mark.parametrize('scale', [.8, 1, 1.25])
@pytest.mark.parametrize('spacer', [False, True])
def test_antialiased_divider_recovers_merged_period(scale, spacer):
    def item(text, x, y):
        return dict(text=text, x0=x*scale, x1=(x+8)*scale,
                    y0=y*scale, y1=(y+10)*scale)
    image = np.full((220, 500), 255, dtype='uint8')
    # A pale but continuous divider, absent only in Tuesday's merged cell.
    cv2.line(image, (219, 30), (219, 180), 180, 1)
    image[82:132, 217:222] = 255
    image = cv2.resize(image, None, fx=scale, fy=scale)
    items = [item(str(p), 50+p*30, 0) for p in range(1, 12)]
    if spacer:
        items[4].update(cell_left=190*scale,cell_right=215*scale)
        items[5].update(cell_left=220*scale,cell_right=245*scale)
    for day, date, y in [('Monday','2026-11-02',50),
                         ('Tuesday','2026-11-03',100),
                         ('Wednesday','2026-11-04',150)]:
        items.extend([item(day, 10, y), item(date, 10, y+12)])
    blocks = [dict(date='2026-11-03', period=5, course='ACS')]
    recovered = sc._english_ocr_merged_cell_supplements_from_image(items, blocks, image)
    assert any(b['date'] == '2026-11-03' and b['period'] == 6 for b in recovered) == (not spacer)


def test_border_header_band_removes_unreadable_time_but_keeps_adjacent_course(monkeypatch):
    from core import bordered_timetables as bt

    def item(text, x, y):
        return dict(text=text, x0=x, x1=x+12, y0=y, y1=y+10, page=0)
    headers = [{**item(str(p),100+p*30,45), 'border_header':True,
                'header_top':40, 'header_bottom':65} for p in range(1,12)]
    noise, course = item('1620号-',280,48), item('ACS',280,68)
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:[noise,course])
    class Doc:
        def __getitem__(self,index): return object()
        def close(self): pass
    monkeypatch.setattr(sc,'_open_pdf_document',lambda *a:Doc())
    monkeypatch.setattr(sc,'_page_image',lambda *a:np.zeros((200,600,3),dtype='uint8'))
    monkeypatch.setattr(bt,'english_header_items',lambda *a:headers)
    seen=[]
    def parse(items,name):
        seen.extend(items)
        return [dict(date='2026-11-02',period=1)]
    monkeypatch.setattr(sc,'_parse_english_grid_page_items_from_pdf_words',parse)
    monkeypatch.setattr(sc,'_english_ocr_merged_cell_supplements',lambda *a:[])
    sc._parse_english_ocr_week_grid({},'测试成员-英方课表.pdf')
    assert not any(i['text']=='1620号-' for i in seen)
    assert any(i['text']=='ACS' for i in seen)


def test_dated_row_boundaries_exclude_corrupt_time_header():
    from core.bordered_timetables import english_course_row_items

    image = np.full((240, 400), 255, dtype='uint8')
    for y in [20, 40, 90, 140, 190, 210]:
        cv2.line(image, (10,y), (390,y), 0, 1)
    def item(text, y):
        return dict(text=text,x0=10,x1=80,y0=y,y1=y+10)
    items = [item('1620号-',25),item('6',25),item('ACS',45)]
    for weekday,date,y in [('Monday','2026-11-02',50),
                           ('Tuesday','2026-11-03',100),
                           ('Wednesday','2026-11-04',150)]:
        items += [item(weekday,y),item(date,y+12)]
    filtered = english_course_row_items(image,items)
    assert not any(i['text']=='1620号-' for i in filtered)
    assert any(i['text']=='ACS' for i in filtered)
    assert any(i['text']=='6' for i in filtered)


def test_partial_first_row_does_not_disable_later_header_filtering():
    from core.bordered_timetables import english_course_row_items

    image=np.full((240,400),255,dtype='uint8')
    for y in [90,140,190,210]:
        cv2.line(image,(10,y),(390,y),0,1)
    def item(text,y):
        return dict(text=text,x0=10,x1=80,y0=y,y1=y+10)
    items=[item('ACS',15),item('1620号-',195)]
    for weekday,date,y in [('Monday','2026-11-02',50),
                           ('Tuesday','2026-11-03',100),
                           ('Wednesday','2026-11-04',150)]:
        items += [item(weekday,y),item(date,y+12)]
    result=english_course_row_items(image,items)
    assert any(i['text']=='ACS' for i in result)
    assert not any(i['text']=='1620号-' for i in result)


def test_fallback_template_is_independent_of_header_page_order(monkeypatch):
    def item(text,x,y,page):
        return dict(text=text,x0=x,x1=x+12,y0=y,y1=y+10,page=page)
    headers=[item(str(p),base+p*40,0,page)
             for page,base in [(0,100),(1,130)] for p in range(1,12)]
    dated=[item('Monday',10,40,2),item('2026-11-02',10,52,2),item('ACS',225,40,2)]
    current=headers+dated
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:current)
    monkeypatch.setattr(sc,'_open_pdf_document',lambda *a:None)
    monkeypatch.setattr(sc,'_english_ocr_merged_cell_supplements',lambda *a:[])
    def parse():
        return {(str(b['date']),b['period'],b['course']) for b in
                sc._parse_english_ocr_week_grid({},'测试成员-英方课表.pdf')}
    before=parse()
    current=[{**i,'page':1-i['page']} if i['page']<2 else i for i in current]
    assert parse()==before


def test_header_with_crossed_period_columns_is_not_a_template():
    items=[dict(text=str(p),x0=100+p*40,x1=110+p*40,y0=0,y1=10)
           for p in range(1,12)]
    # A misread time was labelled "1" in the sixth column.
    items[0].update(x0=550,x1=560)
    assert sc._find_english_period_header_rows(items)==[]
