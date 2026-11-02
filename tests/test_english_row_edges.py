import cv2
import numpy as np
import pytest

from core import schedule_core as sc


@pytest.mark.parametrize('first_pair_gap', [20, 32])
def test_one_narrow_ocr_pair_does_not_suppress_other_merged_cells(first_pair_gap):
    image = np.full((250,900),255,dtype='uint8')
    # Periods 5/6 have a genuine shared divider, absent only in Tuesday's
    # merged cell. The 6/7 gap contains a spacer and must not be bridged.
    for x in [475,535]:
        cv2.line(image,(x,30),(x,96),0,1)
        cv2.line(image,(x,160),(x,230),0,1)
    centers=[100,100+first_pair_gap,200,250,450,500,570,620,740,790,840]
    def item(text,x,y):
        return dict(text=text,x0=x-4,x1=x+4,y0=y,y1=y+10)
    items=[item(str(p),center,0) for p,center in enumerate(centers,1)]
    for weekday,date,y in [('Monday','2026-11-02',60),
                           ('Tuesday','2026-11-03',120),
                           ('Wednesday','2026-11-04',180)]:
        items.extend([item(weekday,10,y),item(date,10,y+12)])
    blocks=[dict(date='2026-11-03',period=6,course='ACS')]
    added=sc._english_ocr_merged_cell_supplements_from_image(items,blocks,image)
    assert any(b['period']==5 for b in added)
    assert not any(b['period']==7 for b in added)
