"""Exercise the production OCR route, including the rendered grid geometry."""
import cv2
import numpy as np
import pytest

from core import schedule_core as sc


def grid_page(scale=1.0, padding=0, course_code=False):
    image = np.full((600, 1100, 3), 255, dtype=np.uint8)
    widths = [100, 50, 50, 10, 50, 50, 180, 50, 50, 20, 50, 50, 70, 50, 50, 50]
    xs = [20]
    for width in widths:
        xs.append(xs[-1] + width)
    # A continued row precedes the next printed header. The page title is
    # just above the cut-off table, close enough to fool midpoint row bands.
    for y in [100, 160, 220, 280, 300, 330, 390, 450, 510, 570]:
        cv2.line(image, (xs[0], y), (xs[-1], y), (0, 0, 0), 1)
    for x in xs:
        cv2.line(image, (x, 60), (x, 280), (0, 0, 0), 1)
        cv2.line(image, (x, 300), (x, 570), (0, 0, 0), 1)
    # 7/8 are merged only in the incomplete first row.
    image[60:100, xs[11]-1:xs[11]+2] = 255
    items = []
    def add(text, x, y, width=40):
        items.append(dict(text=text,x0=x,y0=y,x1=x+width,y1=y+10,page=0))
        cv2.putText(image,text,(round(x),round(y+9)),cv2.FONT_HERSHEY_SIMPLEX,.3,(0,0,0),1)
    add('Timetable for Example', 550, 38, 150)
    for day, date, y in [('Tuesday','2026-11-10',65),('Wednesday','2026-11-11',110),
                         ('Thursday','2026-11-12',170),('Friday','2026-11-13',230),
                         ('Monday','2026-11-16',340),('Tuesday','2026-11-17',400),
                         ('Wednesday','2026-11-18',460),('Thursday','2026-11-19',520)]:
        add(day, 25, y, 80)
        add(date, 25, y+12, 80)
    columns = [1,2,4,5,7,8,10,11,13,14,15]
    for p, col in enumerate(columns, 1):
        add(str(p), (xs[col]+xs[col+1])/2-4, 310, 8)
    center = (xs[10]+xs[12])/2
    if course_code:
        add('ABC',center-12,60,24)
    add('L3B-7',center-20,72,40)
    add('8401',center-15,84,30)
    # A regular, separate single-period course must stay a single period.
    add('XYZ', (xs[7]+xs[8])/2-12, 175, 24)
    add('L3B-7', (xs[7]+xs[8])/2-20, 188, 40)
    add('8302', (xs[7]+xs[8])/2-15, 201, 30)
    if scale != 1:
        image = cv2.resize(image, None, fx=scale, fy=scale)
    if padding:
        image = cv2.copyMakeBorder(image,padding,padding,padding,padding,cv2.BORDER_CONSTANT,value=(255,255,255))
    for item in items:
        for axis in ['x0','x1','y0','y1']:
            item[axis] = item[axis]*scale+padding
    return image, items


def parse_page(monkeypatch, image, items, source=None):
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:items)
    class Doc:
        def __getitem__(self,index): return object()
        def close(self): pass
    monkeypatch.setattr(sc,'_open_pdf_document',lambda *a:Doc())
    monkeypatch.setattr(sc,'_page_image',lambda *a:image)
    return sc._parse_english_ocr_week_grid(source if source is not None else {}, '测试成员-英方课表.pdf')


@pytest.mark.parametrize('scale,padding', [(1,0),(.8,0),(1.25,0),(1,100)])
def test_partial_course_cell_cannot_become_free(monkeypatch, scale, padding):
    image, items = grid_page(scale, padding)
    blocks = parse_page(monkeypatch,image,items)
    slots = {(b['date'],b['period']) for b in blocks}
    assert ('2026-11-10',7) in slots
    assert ('2026-11-10',8) in slots
    assert ('2026-11-10',5) not in slots  # page title, not a course
    assert ('2026-11-12',5) in slots
    assert ('2026-11-12',6) not in slots  # visible divider is retained
    assert not any('Timetable' in b['course'] for b in blocks)
    assert not any(b['date']=='2026-11-11' for b in blocks)  # truly empty row


def test_visible_course_survives_continuation_crop(monkeypatch):
    image, items = grid_page(course_code=True)
    blocks = parse_page(monkeypatch,image,items)
    assert {b['period'] for b in blocks if b['date']=='2026-11-10' and b['course']=='ABC'} == {7,8}
    assert not any('Timetable' in b['course'] for b in blocks)


@pytest.mark.parametrize('scale', [1, .8, 1.25])
def test_short_continuation_uses_observed_edge_not_full_row_kernel(monkeypatch, scale):
    image, items = grid_page(course_code=True)
    # Only the bottom 25 pixels of a 60-pixel course row survive the page cut.
    # Its borders are shorter than the normal body-divider opening kernel.
    image[60:76] = 255
    items = [i for i in items if i['y0'] >= 76 or i['y0'] < 60]
    for i in items:
        if i['text'] == '2026-11-10':
            i['y0'], i['y1'] = 78, 88
    if scale != 1:
        image = cv2.resize(image, None, fx=scale, fy=scale)
        for item in items:
            for axis in ['x0', 'x1', 'y0', 'y1']:
                item[axis] *= scale
    blocks = parse_page(monkeypatch, image, items)
    assert {b['period'] for b in blocks if b['date'] == '2026-11-10'} == {7, 8}
    assert not any('Timetable' in b['course'] for b in blocks)


def test_missing_ocr_tokens_still_expose_nonempty_cell(monkeypatch):
    image, items = grid_page()
    items = [i for i in items if i['text'] not in {'L3B-7','8401'}]
    blocks = parse_page(monkeypatch,image,items)
    pending = [b for b in blocks if b['date']=='2026-11-10']
    assert {b['period'] for b in pending} == {7,8}
    assert all(b['provenance']['cell_text_incomplete'] for b in pending)


def test_pale_rescaled_grid_keeps_day_boundaries(monkeypatch):
    image, items = grid_page(scale=.8, course_code=True)
    # Thin lines lighten during PDF resampling, but letters remain readable.
    grey = image.mean(axis=2)
    for y in range(len(image)):
        if (grey[y] < 220).sum() > 500:
            image[y, grey[y] < 220] = 210
    blocks = parse_page(monkeypatch,image,items)
    assert {b['period'] for b in blocks if b['date']=='2026-11-10' and b['course']=='ABC'} == {7,8}
    assert not any(b['date']=='2026-11-11' for b in blocks)


@pytest.mark.parametrize('shade', [195, 211, 225])
def test_shaded_header_keeps_clipped_course_occupancy(monkeypatch, shade):
    image, items = grid_page()
    # Browser print backgrounds shade the whole numbered header, including
    # spacers. This must not turn the header into one giant vertical divider.
    image = np.concatenate([image[:330], np.repeat(image[329:330], 6, axis=0), image[330:]])
    for item in items:
        if item['y0'] >= 330:
            item['y0'] += 6
            item['y1'] += 6
    region = image[300:337, 20:951]
    region[np.all(region == 255, axis=2)] = shade
    blocks = parse_page(monkeypatch, image, items)
    assert {b['period'] for b in blocks if b['date'] == '2026-11-10'} == {7, 8}
    assert all(b.get('provenance', {}).get('method') == 'dated_visible_cell' for b in blocks)


def test_midday_source_evidence_is_not_mapped_into_adjacent_periods(monkeypatch):
    image, items = grid_page()
    items.append(dict(text='NOON', x0=350, x1=390, y0=115, y1=125, page=0))
    source = dict(bytes=b'anonymous-grid')
    blocks = parse_page(monkeypatch,image,items,source)
    assert not any(b['course']=='NOON' for b in blocks)
    notes = sc.english_grid_evidence(source)
    assert len(notes)==1 and notes[0]['kind']=='unnumbered_midday'
    assert notes[0]['date']=='2026-11-11'


def test_popup_details_are_reported_as_an_obstructed_source(monkeypatch):
    image, items = grid_page()
    for y,text in [(115,'Start: 15:00'),(128,'Length: 90'),(141,'Module: ABC')]:
        items.append(dict(text=text,x0=800,x1=850,y0=y,y1=y+10,page=0))
    source = dict(bytes=b'anonymous-grid')
    parse_page(monkeypatch,image,items,source)
    notes = sc.english_grid_evidence(source)
    assert any(n['kind']=='overlay' and n['page']==0 for n in notes)


def test_real_course_wins_over_incomplete_continuation():
    base = dict(name='测试成员',week=11,weekday='周二',period=7)
    known = {**base,'course':'ABC'}
    partial = {**base,'course':'待核对占用（课程文字不完整）',
               'provenance':{'cell_text_incomplete':True}}
    for blocks in [[known,partial],[partial,known]]:
        assert sc._dedupe_blocks(blocks)[0]['course'] == 'ABC'


def test_incomplete_cell_has_specific_quality_gate():
    from core.models import CourseBlock, MemberSchedule
    from core.quality import evaluate_quality
    block = CourseBlock(name='测试成员',source_type='英方',week=11,weekday='周二',
                        periods=[7],date='2026-11-10',course='待核对占用（课程文字不完整）',
                        page=1, bbox=(100,50,200,80),
                        provenance={'cell_text_incomplete':True,'periods':[7,8]})
    member = MemberSchedule(name='测试成员',has_chinese=True,has_english=True,blocks=[block])
    state, issues = evaluate_quality(file_records=[], members=[member], enforce_identity=False)
    assert state == 'needs_review'
    assert len(issues) == 1 and issues[0].code == 'ENGLISH_CELL_TEXT_INCOMPLETE'
    assert issues[0].block_id == block.block_id
    assert '第 2 页' in issues[0].message and '2026-11-10' in issues[0].message
    block.course = 'ABC'  # a recorded correction resolves this derived issue
    assert evaluate_quality(file_records=[],members=[member],enforce_identity=False)[0]=='accepted'
