"""Anonymous regressions for semantic notices in merged timetable cells."""
import cv2
import numpy as np
import pytest

from core import schedule_core as sc
from core.bordered_timetables import parse_chinese_bordered_image


@pytest.mark.parametrize('notice', ['元旦', '元旦节', '中秋节', '国庆节', '研究生考试'])
def test_standard_calendar_notice_is_not_a_course(notice):
    assert sc._is_empty_or_non_class(notice)


@pytest.mark.parametrize('notice', ['全国英语四六级', '全国英X四六级', '全国英X四六级考试'])
def test_cet_event_marker_tolerates_bounded_ocr_noise(notice):
    assert sc._is_empty_or_non_class(notice)


@pytest.mark.parametrize('course', ['传统节日文化', '中秋节文化专题',
                                    '研究生考试辅导', '全国英语四六级培训',
                                    '全国英语四六级辅导', '全国英语四六级课程'])
def test_notice_words_inside_course_title_do_not_clear_it(course):
    assert not sc._is_empty_or_non_class(course)
    assert sc._is_chinese_full_term_course_text(course)


@pytest.mark.parametrize('scale,padding', [(1,0), (.8,100), (1.25,0), (1,300)])
@pytest.mark.parametrize('text,is_notice', [('元旦节',True),('中秋节文化',False)])
def test_merged_holiday_notice_does_not_create_fragment_occupancy(scale, padding, text, is_notice):
    image=np.full((900,1400),255,dtype=np.uint8)
    xs=[20,60]
    for _ in range(7):
        for width in [12,48,10,24,36,48]:
            xs.append(xs[-1]+width)
    for y in [40,55,*range(70,491,21),550]:
        cv2.line(image,(20,y),(xs[-1],y),0,1)
    for x in xs:
        cv2.line(image,(x,55),(x,490),0,1)
    # A Saturday-wide merged notice. Its glyphs cross ordinary period columns.
    day=5
    left_index=1+day*6
    left,right=xs[left_index],xs[left_index+6]
    top,bottom=70+9*21,70+10*21
    for x in xs[left_index+1:left_index+6]:
        image[top+1:bottom,x-1:x+2]=255
    center=(left+right)/2
    cv2.putText(image,'FEST',(round(center)-21,top+16),
                cv2.FONT_HERSHEY_SIMPLEX,.5,0,1)
    items=[dict(text=f'{week}周',x0=22,x1=45,
                y0=70+(week-1)*21+4,y1=70+(week-1)*21+12)
           for week in [1,10,20]]
    items.append(dict(text=text,x0=center-22,x1=center+22,y0=top+4,y1=top+17))
    if scale!=1:
        image=cv2.resize(image,None,fx=scale,fy=scale)
    if padding:
        image=cv2.copyMakeBorder(image,padding,padding,padding,padding,
                                 cv2.BORDER_CONSTANT,value=255)
    for item in items:
        for axis in ['x0','x1','y0','y1']:
            item[axis]=item[axis]*scale+padding
        item.update(page_width=image.shape[1],page_height=image.shape[0])
    blocks=parse_chinese_bordered_image(image,'测试成员',items)
    assert any(b['week']==10 and b['weekday']=='周六' for b in blocks) == (not is_notice)
