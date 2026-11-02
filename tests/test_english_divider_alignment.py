import cv2
import numpy as np
import pytest

from core import schedule_core as sc


@pytest.mark.parametrize('width',[5,7,15])
def test_thick_real_divider_keeps_continuous_support_on_grey_fill(width):
    from core.bordered_timetables import vertical_grid_lines

    image=np.full((180,160),211,dtype='uint8')
    image[20:160,80-width//2:80-width//2+width]=0
    lines=vertical_grid_lines(image,50)
    assert float((lines[30:150,71:90].max(axis=1)>0).mean()) > .7


@pytest.mark.parametrize('current_divider', [True, False])
@pytest.mark.parametrize('header_after', [False, True])
@pytest.mark.parametrize('background', [255, 211])
def test_merged_cell_uses_current_rows_local_divider(current_divider, header_after, background):
    """A border can shift a few pixels between rows after PDF rasterization."""
    image = np.full((250, 600), 255, dtype='uint8')
    # A merged course can have a grey fill. Uniform fill is not a divider.
    image[90:160, 80:160] = background
    # Other rows have a border at x=122. Tuesday's border, when present,
    # is at x=117; both are within the same shared-edge search neighbourhood.
    cv2.line(image, (122, 30), (122, 96), 0, 1)
    cv2.line(image, (122, 160), (122, 230), 0, 1)
    if current_divider:
        cv2.line(image, (117, 90), (117, 160), 0, 1)
    # A short letter stroke in a merged course must not become a divider.
    cv2.line(image, (117, 110), (117, 122), 0, 1)

    def item(text, x, y):
        return dict(text=text, x0=x-4, x1=x+4, y0=y, y1=y+10)

    items = [item(str(period), 100+(period-1)*40, 200 if header_after else 0)
             for period in range(1, 12)]
    for weekday, date, y in [
        ('Monday', '2026-11-02', 60),
        ('Tuesday', '2026-11-03', 120),
        ('Wednesday', '2026-11-04', 180),
    ]:
        items.extend([item(weekday, 10, y), item(date, 10, y+12)])
    blocks = [dict(date='2026-11-03', period=1, course='ACS')]
    added = sc._english_ocr_merged_cell_supplements_from_image(items, blocks, image)
    assert any(block['period'] == 2 for block in added) is (not current_divider)


def test_header_only_dividers_survive_page_padding():
    from core.bordered_timetables import english_header_items

    image = np.full((900, 1000), 255, dtype='uint8')
    widths = [50,45,45,25,45,45,140,45,45,20,45,45,60,45,45,45]
    xs = [35]
    for width in widths:
        xs.append(xs[-1]+width)
    for y in [40,72,127,182,237,292,347]:
        cv2.line(image, (xs[0],y), (xs[-1],y), 0, 1)
    for x in [xs[0],xs[-1]]:
        cv2.line(image, (x,40), (x,347), 0, 1)
    # Header dividers are 32px high; merged body cells do not continue them.
    for x in xs[1:-1]:
        cv2.line(image, (x,40), (x,72), 0, 1)
    before = english_header_items(image)
    padded = cv2.copyMakeBorder(image,300,300,300,300,cv2.BORDER_CONSTANT,value=255)
    after = english_header_items(padded)
    assert len(before) == 11
    expected = {(int(i['text']), (i['x0']+i['x1'])/2) for i in before}
    actual = {(int(i['text']), (i['x0']+i['x1'])/2-300) for i in after}
    assert actual == expected


@pytest.mark.parametrize('current_divider', [True, False])
def test_interpolated_date_gets_same_merge_check_as_printed_dates(current_divider):
    image = np.full((390,600),255,dtype='uint8')
    cv2.line(image,(122,30),(122,210),0,1)
    cv2.line(image,(122,280),(122,370),0,1)
    if current_divider:
        cv2.line(image,(117,210),(117,280),0,1)
    cv2.line(image,(117,230),(117,242),0,1)
    def item(text,x,y):
        return dict(text=text,x0=x-4,x1=x+4,y0=y,y1=y+10)
    items=[item(str(p),100+(p-1)*40,0) for p in range(1,12)]
    # Main cell parsing interpolates Thursday between Wednesday and Friday.
    for weekday,date,y in [('Monday','2026-11-02',60),
                           ('Tuesday','2026-11-03',120),
                           ('Wednesday','2026-11-04',180),
                           ('Friday','2026-11-06',300)]:
        items.extend([item(weekday,10,y),item(date,10,y+12)])
    blocks=[dict(date='2026-11-05',period=1,course='ACS')]
    added=sc._english_ocr_merged_cell_supplements_from_image(items,blocks,image)
    assert any(b['date']=='2026-11-05' and b['period']==2 for b in added) is (not current_divider)
