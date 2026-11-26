from core import schedule_core as sc


def test_browser_footer_date_cannot_override_a_clipped_weekday():
    def i(text,y):return dict(text=text,x0=20,x1=90,y0=y,y1=y+10)
    items=[i('Monday',100),i('2026-12-07',112),i('Tuesday',150),i('2026-12-08',162),
           i('Wednesday',200),i('https://example.test/?start=2026-10-22',216)]
    # Two full-row anchors suffice even when the page ends within the third.
    rows=sc._find_english_day_rows(items)
    assert any(str(r['date'])=='2026-12-09' for r in rows)
    assert not sc._is_english_date_token(items[-1]['text'])


def test_date_only_page_continuation_requires_two_calendar_anchors():
    def i(text,y):return dict(text=text,x0=20,x1=90,y0=y,y1=y+10)
    items=[i('2026-12-09',20),i('Thursday',55),i('2026-12-10',67),
           i('Friday',105),i('2026-12-11',117)]
    assert str(sc._find_english_day_rows(items)[0]['date'])=='2026-12-09'
    assert not any(str(r['date'])=='2026-12-09' for r in sc._find_english_day_rows(items[:3]))


def test_unreadable_week_end_date_requires_two_aligned_anchors():
    def item(text,y,x=20):return dict(text=text,x0=x,x1=x+70,y0=y,y1=y+10)
    items=[]
    for offset,day in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday']):
        items.extend([item(day,100+offset*50),item(f'2026-11-{23+offset}' if offset<4 else '2026-1↑27',112+offset*50)])
    rows=sc._find_english_day_rows(items)
    assert [str(r['date']) for r in rows]==[f'2026-11-{n}' for n in range(23,28)]
    assert rows[-1]['date_evidence']=='weekday_and_two_same_week_anchors'
    assert len(sc._find_english_day_rows(items[-4:]))==1  # one anchor is insufficient
    # A readable but contradictory date must not be silently replaced.
    items[-1]['text']='2026-11-28'
    assert len(sc._find_english_day_rows(items))==4


def test_dated_rows_survive_page_translation():
    def item(text, x, y):
        return dict(text=text, x0=x, x1=x+70, y0=y, y1=y+12)
    original = [item('Monday', 12, 40), item('2026-11-02', 12, 54)]
    shifted = [{**i, 'x0': i['x0']+240, 'x1': i['x1']+240,
                'y0': i['y0']+100, 'y1': i['y1']+100} for i in original]
    rows = sc._find_english_day_rows(shifted)
    assert len(rows) == 1
    assert str(rows[0]['date']) == '2026-11-02'


def test_date_is_paired_with_its_weekday_column():
    items = [dict(text=t, x0=x, x1=x+70, y0=y, y1=y+12)
             for t,x,y in [('Monday',240,40),('2026-11-02',240,54),
                           ('2026-11-09',600,54)]]
    rows = sc._find_english_day_rows(items)
    assert len(rows) == 1 and str(rows[0]['date']) == '2026-11-02'


def test_headerless_page_can_use_header_from_a_later_page(monkeypatch):
    def item(text, x, y, page):
        return dict(text=text, x0=x, x1=x+12, y0=y, y1=y+10, page=page)
    items = [item(str(p),100+p*40,0,1) for p in range(1,12)]
    items += [item('Monday',10,40,0),item('2026-11-02',10,52,0),
              item('ACS',140,40,0)]
    monkeypatch.setattr(sc, '_extract_pdf_ocr_items', lambda *a, **k: items)
    monkeypatch.setattr(sc, '_open_pdf_document', lambda *a: None)
    monkeypatch.setattr(sc, '_english_ocr_merged_cell_supplements', lambda *a: [])
    blocks = sc._parse_english_ocr_week_grid({}, '测试成员-英方课表.pdf')
    assert any(str(b['date']) == '2026-11-02' and b['weekday'] == '周一' and b['period'] == 1
               for b in blocks)
