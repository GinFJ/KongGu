import cv2
import numpy as np
import pytest

from core import schedule_core as sc


def test_ocr_name_stops_at_joined_field_label():
    assert sc._extract_chinese_name_from_text('姓名张三班级202601') == '张三'
    assert sc._extract_chinese_name_from_text('姓名：欧阳明月 学号123') == '欧阳明月'
    assert sc._extract_chinese_name_from_text('姓名：李四\n班级202601') == '李四'


@pytest.mark.parametrize('text', ['教研', '救研', '数研'])
def test_research_label_ocr_variants_are_not_student_classes(text):
    assert sc._is_empty_or_non_class(text)
    assert not sc._is_empty_or_non_class('数学研究方法')


def test_english_headers_keep_unlabelled_spacers():
    from core.bordered_timetables import english_header_items
    image=np.full((600,1000),255,dtype=np.uint8)
    widths=[50,45,45,25,45,45,140,45,45,20,45,45,60,45,45,45]
    xs=[35]
    for width in widths:xs.append(xs[-1]+width)
    for y in [40,65,120,175,230,285,340]:
        cv2.line(image,(xs[0],y),(xs[-1],y),0,1)
    for x in xs:cv2.line(image,(x,40),(x,340),0,1)
    items=english_header_items(image)
    centers={int(i['text']):(i['x0']+i['x1'])/2 for i in items}
    assert centers[5]==(xs[7]+xs[8])/2
    assert centers[7]==(xs[10]+xs[11])/2
    assert len(centers)==11


def test_first_row_of_english_continuation_keeps_course_above_date():
    def item(text,x,y,w=15):return dict(text=text,x0=x,x1=x+w,y0=y,y1=y+10)
    items=[item(str(p),80+p*40,0) for p in range(1,12)]
    items.extend([item('Tuesday',5,40,70),item('2026-05-12',5,50,70),
                  item('Wednesday',5,100,70),item('2026-05-13',5,110,70),item('IAP',280,30)])
    blocks=sc._parse_english_grid_page_items_from_pdf_words(items,'匿名')
    assert any(b['date']=='2026-05-12' and b['period']==5 and b['course']=='IAP' for b in blocks)


@pytest.mark.parametrize('scale,padding', [(1, 0), (1, 500), (.8, 0), (1.25, 0)])
def test_full_term_raster_uses_visible_unequal_columns_and_twenty_rows(scale, padding):
    """A wide 3-4 column must not be mistaken for a uniform 1-2 column."""
    from core.bordered_timetables import parse_chinese_bordered_image

    image = np.full((900, 1400), 255, dtype=np.uint8)
    widths = [12, 48, 10, 24, 36, 48]
    xs = [20, 60]
    for _day in range(7):
        for width in widths:
            xs.append(xs[-1] + width)
    for y in [40, 55, *range(70, 491, 21), 550]:
        cv2.line(image, (20, y), (xs[-1], y), 0, 1)
    for x in xs:
        cv2.line(image, (x, 55), (x, 490), 0, 1)
    # Week 10, Tuesday 3-4: solid glyph-like marks well inside the real cell.
    left, right = xs[8], xs[9]
    top = 70 + 9 * 21
    cv2.putText(image, 'ABC', (left + 4, top + 15), cv2.FONT_HERSHEY_SIMPLEX, .35, 0, 1)
    items = [
        {'text': f'{week}周', 'x0': 22, 'x1': 45, 'y0': 70 + (week-1)*21+4,
         'y1': 70 + (week-1)*21+12, 'page_width': 1400, 'page_height': 900}
        for week in [1, 10, 20]
    ]
    if scale != 1:
        image = cv2.resize(image, None, fx=scale, fy=scale)
    if padding:
        image = cv2.copyMakeBorder(image, padding, padding, padding, padding,
                                   cv2.BORDER_CONSTANT, value=255)
    for item in items:
        for axis in ['x0', 'x1', 'y0', 'y1']:
            item[axis] = item[axis]*scale + padding
        item['page_width'] = image.shape[1]
        item['page_height'] = image.shape[0]
    blocks = parse_chinese_bordered_image(image, '测试成员', items)
    occupied = {(b['week'], b['weekday'], b['period']) for b in blocks}
    assert (10, '周二', 3) in occupied
    assert (10, '周二', 4) in occupied
    assert (10, '周二', 1) not in occupied
    assert not any(week > 20 for week, _, _ in occupied)
