"""A popup is ignorable only when the original PDF proves its cells empty."""
import fitz
import numpy as np
import pytest

from core import schedule_core as sc


def test_adjacent_grid_stroke_at_raster_boundary_is_not_course_ink():
    from core.english_popup_underlay import cell_is_proven_blank
    borders = [[0, 0, 1, 51], [49, 0, 50, 51], [0, 0, 50, 1], [0, 49, 50, 50]]
    # The prior row ends just inside a rounded raster boundary. Full borders
    # still surround this cell; the short adjacent segment is border ink.
    adjacent = [0, -40, 1.5, 1.4]
    evidence = dict(borders=borders, content=[dict(bbox=r, border_eligible=True)
                    for r in borders+[adjacent]])
    assert cell_is_proven_blank(evidence, (0, 0, 50, 50))
    # Real non-border paint near that same edge must still prevent emptiness.
    evidence['content'].append(dict(bbox=[1, 1, 4, 7], border_eligible=False))
    assert not cell_is_proven_blank(evidence, (0, 0, 50, 50))
from tests.test_english_midday_axis import clock_grid


def popup_source(monkeypatch, *, occupied=False, raster=False, frames=False):
    _,items=clock_grid()
    doc=fitz.open();page=doc.new_page(width=1100,height=600)
    edges=[20,140,185,190,235,265,310,315,360,520,565,570,615,635,680,685,730,800,845,850,895,900,945]
    for x in edges:
        page.draw_line((x,40),(x,530),width=.7)
    for y in [40,80,170,260,350,440,530]:
        page.draw_line((20,y),(945,y),width=.7)
    if frames:
        for y in [80,170,260,350,440]:
            page.draw_rect(fitz.Rect(20,y,945,y+90),width=.7)
    # This fixture tests the popup only; do not add the lunch labels.
    items=[i for i in items if i['text'] not in {'AAA','BBB','CCC','L3A-1'}]
    # Browser query controls above the table must not enlarge the popup bbox.
    for x,label in [(20,'Teacher:'),(200,'Room:'),(350,'Group:')]:
        items.append(dict(text=label,x0=x,x1=x+70,y0=10,y1=20,page=0))
    for item in items:
        page.insert_text((item['x0'],item['y0']+9),item['text'],fontsize=8)
    if occupied:
        page.insert_text((810,310),'CLASS',fontsize=8)
    if raster:
        bitmap=page.get_pixmap().tobytes('png')
        doc.close();doc=fitz.open();page=doc.new_page(width=1100,height=600)
        page.insert_image(page.rect,stream=bitmap)
    popup=fitz.open();pp=popup.new_page(width=150,height=270)
    labels=['Start: 2026-12-28 14:30','Length: 2 periods','Module: EXAMPLE',
            'Group: L3A-1','Room: 8001','Teacher: Example']
    for n,label in enumerate(labels):
        pp.insert_text((8,18+n*35),label,fontsize=7,color=(.5,.5,.5))
        items.append(dict(text=label,x0=803,y0=260+10+n*35,x1=938,y1=280+n*35,page=0))
    page.insert_image(fitz.Rect(795,260,945,530),stream=pp.get_pixmap().tobytes('png'))
    # Production renders at 120 dpi; keep the tested pixel geometry identical
    # to the clock-grid fixture while exercising real PDF drawing metadata.
    small=fitz.open();small_page=small.new_page(width=660,height=360)
    small_page.show_pdf_page(small_page.rect,doc,0)
    data=small.tobytes();small.close();doc.close();popup.close()
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:items)
    monkeypatch.setattr(sc,'_open_pdf_document',lambda *a:fitz.open(stream=data,filetype='pdf'))
    def render(source,page,index,dpi):
        pix=page.get_pixmap(dpi=dpi,alpha=False)
        return np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
    monkeypatch.setattr(sc,'_page_image',render)
    return {'content':data}


@pytest.mark.parametrize('frames',[False,True])
def test_empty_vector_underlay_does_not_create_fake_evening_courses(monkeypatch,frames):
    source=popup_source(monkeypatch,frames=frames)
    blocks=sc._parse_english_ocr_week_grid(source,'测试成员-英方课表.pdf')
    assert not [b for b in blocks if b['period'] in {9,10,11}]
    notes=source['_english_grid_evidence']
    assert not any(n['kind']=='overlay' for n in notes)
    assert any(n['kind']=='overlay_verified_empty' for n in notes)


@pytest.mark.parametrize('occupied,raster',[(True,False),(False,True)])
def test_popup_cannot_hide_course_or_unverifiable_raster(monkeypatch,occupied,raster):
    source=popup_source(monkeypatch,occupied=occupied,raster=raster)
    sc._parse_english_ocr_week_grid(source,'测试成员-英方课表.pdf')
    assert any(n['kind']=='overlay' for n in source['_english_grid_evidence'])
