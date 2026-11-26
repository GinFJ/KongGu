"""A short legend row cannot become another teaching week."""
import cv2
import numpy as np
import pytest

from core.bordered_timetables import parse_chinese_bordered_image


@pytest.mark.parametrize('weeks',[18,21,22])
def test_week_column_boundary_separates_body_from_short_legend(weeks):
    image=np.full((900,1400),255,dtype=np.uint8)
    xs=[20,60]
    for _ in range(7):
        for width in [12,48,10,24,36,48]:xs.append(xs[-1]+width)
    bottom=70+weeks*21
    for y in [40,55,*range(70,bottom+1,21),bottom+34]:
        cv2.line(image,(20,y),(xs[-1],y),0,1)
    for x in xs:cv2.line(image,(x,55),(x,bottom),0,1)
    # Legend has the same outer boundary and a height below the old 1.8 cutoff,
    # but the week-number column ends at the actual final timetable row.
    for x in [xs[0],xs[15],xs[29],xs[-1]]:
        cv2.line(image,(x,bottom),(x,bottom+34),0,1)
    items=[dict(text=f'{week}周',x0=22,x1=45,y0=70+(week-1)*21+4,y1=70+(week-1)*21+12)
           for week in [1,10,weeks]]
    for y in [bottom-16,bottom+5]:
        cv2.putText(image,'ABC',(xs[2]+5,y+9),cv2.FONT_HERSHEY_SIMPLEX,.3,0,1)
        items.append(dict(text='ABC',x0=xs[2]+4,x1=xs[2]+28,y0=y,y1=y+10))
    blocks=parse_chinese_bordered_image(image,'测试成员',items)
    assert blocks
    assert {b['week'] for b in blocks}=={weeks}
