"""Clock-proportional dated grids must preserve unnumbered midday lessons."""
import cv2
import numpy as np
import pytest

from tests.test_english_cell_completeness import parse_page


def clock_grid(scale=1.0, padding=0, warp_noon=0):
    image = np.full((600, 1100, 3), 255, dtype=np.uint8)
    times = [(490,535),(540,585),(615,660),(665,710),
             (870,915),(920,965),(985,1030),(1035,1080),
             (1150,1195),(1200,1245),(1250,1295)]
    x = lambda minute: round(140 + (minute-490) + (warp_noon if minute >= 870 else 0))
    items = []
    def word(text, px, py, width=40):
        items.append(dict(text=text,x0=px,x1=px+width,y0=py,y1=py+10,page=0))
        cv2.putText(image,text,(round(px),round(py+9)),cv2.FONT_HERSHEY_SIMPLEX,.3,(0,0,0),1)
    for y in [40,80,170,260,350,440,530]:
        cv2.line(image,(20,y),(x(1295),y),(0,0,0),1)
    for edge in sorted({20, *[x(t) for pair in times for t in pair]}):
        cv2.line(image,(edge,40),(edge,530),(0,0,0),1)
    for p,(start,end) in enumerate(times,1):
        word(str(p),(x(start)+x(end))/2-4,48,8)
    for n,day in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday']):
        word(day,25,100+n*90,90)
        word(f'2026-12-{28+n:02}' if n<4 else '2027-01-01',25,115+n*90,100)
    for y,start,end,label in [(80,760,805,'AAA'),(170,810,855,'BBB'),(260,760,855,'CCC')]:
        for edge in [x(start),x(end)]:
            cv2.line(image,(edge,y),(edge,y+90),(0,0,0),1)
        word(label,(x(start)+x(end))/2-12,y+30,24)
        word('L3A-1',(x(start)+x(end))/2-20,y+45,40)
    if scale != 1:
        image=cv2.resize(image,None,fx=scale,fy=scale)
    if padding:
        image=cv2.copyMakeBorder(image,padding,padding,padding,padding,cv2.BORDER_CONSTANT,value=(255,255,255))
    for item in items:
        for key in ['x0','x1','y0','y1']:
            item[key]=item[key]*scale+padding
    return image,items


@pytest.mark.parametrize('scale,padding',[(1,0),(.8,0),(1.2,31)])
def test_midday_clock_axis_survives_scaling_and_padding(monkeypatch,scale,padding):
    image,items=clock_grid(scale,padding)
    source={'bytes':b'anonymous-clock-grid'}
    blocks=parse_page(monkeypatch,image,items,source)
    assert {(b['course'],b['period']) for b in blocks} == {('AAA',12),('BBB',13),('CCC',12),('CCC',13)}
    assert {b['time'] for b in blocks if b['period']==12} == {'12:40-13:25'}
    assert not source.get('_english_grid_evidence')


def test_nonproportional_midday_axis_is_not_guessed(monkeypatch):
    image,items=clock_grid(warp_noon=55)
    source={'bytes':b'anonymous-warped-grid'}
    blocks=parse_page(monkeypatch,image,items,source)
    assert not any(b['period'] in {12,13} for b in blocks)
    assert any(n['kind']=='unnumbered_midday' for n in source['_english_grid_evidence'])


def test_text_layer_rectangle_route_uses_the_same_clock_mapping():
    from core import schedule_core as sc
    _,items=clock_grid()
    rectangles=[(410,80,455,170),(460,170,505,260),(410,260,505,350)]
    unresolved=[]
    blocks=sc._parse_english_grid_page_items_from_rectangles(items,rectangles,'测试成员',unresolved)
    assert {(b['course'],b['period']) for b in blocks} == {('AAA',12),('BBB',13),('CCC',12),('CCC',13)}
    assert not unresolved


@pytest.mark.parametrize('label',['Thursday_','(Thursday)','Thursday.'])
def test_punctuation_at_last_date_row_does_not_drop_courses(monkeypatch,label):
    image,items=clock_grid()
    image=image[:440]
    items=[{**i,'text':label if i['text']=='Thursday' else i['text']}
           for i in items if i['y0']<440]
    items.append(dict(text='DDD',x0=530,x1=554,y0=385,y1=395,page=0))
    cv2.putText(image,'DDD',(530,394),cv2.FONT_HERSHEY_SIMPLEX,.3,(0,0,0),1)
    blocks=parse_page(monkeypatch,image,items,{'bytes':b'clipped-date-row'})
    assert any(b['date']=='2026-12-31' and b['period']==5 and b['course']=='DDD' for b in blocks)


def test_conflicting_date_on_visible_last_row_is_blocking_evidence(monkeypatch):
    image,items=clock_grid()
    image=image[:440]
    items=[{**i,'text':'Thrsdy?' if i['text']=='Thursday' else '2026-12-29' if i['text']=='2026-12-31' else i['text']}
           for i in items if i['y0']<440]
    source={'bytes':b'unreadable-last-weekday'}
    parse_page(monkeypatch,image,items,source)
    assert any(n['kind']=='unresolved_date_row' for n in source['_english_grid_evidence'])
