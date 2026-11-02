"""Anonymous vector timetable through the real PDF-to-occupancy seam."""
import hashlib

import fitz
import pytest

from core import schedule_core as sc
from tools.audit_layout_invariance import transformed


def _outlined_timetable(conflict=False):
    xs=[20,60]
    for _ in range(7):
        for width in [12,48,10,24,36,48]:
            xs.append(xs[-1]+width)
    items=[dict(text=f'{week}周',x0=22,x1=45,
                y0=70+(week-1)*21+4,y1=70+(week-1)*21+12,
                page=0,page_width=1400,page_height=900) for week in [1,10,20]]
    with fitz.open() as doc:
        page=doc.new_page(width=560,height=360)
        def rect(box):
            page.draw_rect(fitz.Rect(*(v/2.5 for v in box)),color=None,fill=(0,0,0))
        for y in [40,55,*range(70,491,21),550]:
            rect((20,y-.4,xs[-1],y+.4))
        for x in xs:
            rect((x-.4,55,x+.4,490))
        for week,text in [(2,'教研'),(3,'教研'),(10,'5L33' if conflict else '未知')]:
            top=70+(week-1)*21
            # Two complete vector glyphs, each with the same closed contour.
            for dx in [0,12]:
                shape=page.new_shape()
                points=[(xs[2]+5+dx,top+5),(xs[2]+12+dx,top+5),
                        (xs[2]+12+dx,top+8),(xs[2]+9+dx,top+8),
                        (xs[2]+9+dx,top+15),(xs[2]+5+dx,top+15)]
                shape.draw_polyline([fitz.Point(x/2.5,y/2.5) for x,y in points])
                shape.finish(color=None,fill=(0,0,0),closePath=True)
                shape.commit()
            items.append(dict(text=text,x0=xs[2]+4,x1=xs[2]+29,
                              y0=top+3,y1=top+17,page=0,page_width=1400,page_height=900))
        top=70+9*21
        rect((xs[5]+5,top+5,xs[5]+9,top+16))
        rect((xs[5]+9,top+10,xs[5]+23,top+14))
        items.append(dict(text='AX55',x0=xs[5]+4,x1=xs[5]+24,
                          y0=top+3,y1=top+17,page=0,page_width=1400,page_height=900))
        return doc.tobytes(),items


@pytest.mark.parametrize('scale,padding',[(1,0),(.8,0),(1.25,0),(1,.2)])
def test_vector_notice_at_pdf_pipeline_is_invariant_and_source_bound(monkeypatch,scale,padding):
    data,items=_outlined_timetable()
    if (scale,padding)!=(1,0):
        data,items=transformed(data,items,scale=scale,padding=padding)
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda _:items)
    source=dict(bytes=data,file_name='测试成员-中方课表.pdf',kind='中方')
    blocks=sc._parse_chinese_full_term_visual_occupancy(source,source['file_name'])
    assert blocks
    assert any(b['week']==10 and b['weekday']=='周一' and b['period']==7 for b in blocks)
    assert not any(b['week']==10 and b['weekday']=='周一' and b['period'] in [3,4] for b in blocks)
    notes=sc.visual_notice_consensus_evidence(source)
    note=next(n for n in notes if n['week']==10)
    assert note['source_hash']==hashlib.sha256(data).hexdigest()
    assert note['parser_signature']==sc.cache_signature()
    assert note['ocr_signature']==sc._ocr_cache_signature()
    assert note['action']=='excluded_from_preview_occupancy' and note['occupancy_changed']
    assert note['requires_review'] and len(note['seeds'])==2
    assert note['match']['method_version']=='same-page-vector-glyph-v1'
    assert note['original_ocr'][0]['text']=='未知'


def test_vector_pipeline_does_not_clear_an_encoded_course(monkeypatch):
    data,items=_outlined_timetable(conflict=True)
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda _:items)
    source=dict(bytes=data,file_name='测试成员-中方课表.pdf',kind='中方')
    blocks=sc._parse_chinese_full_term_visual_occupancy(source,source['file_name'])
    assert any(b['week']==10 and b['weekday']=='周一' and b['period']==3 for b in blocks)
    assert not sc.visual_notice_consensus_evidence(source)


def test_complete_vector_glyph_can_resolve_an_imprecise_ocr_rectangle(monkeypatch):
    data,items=_outlined_timetable()
    target=next(item for item in items if item['text']=='未知')
    target['x0']-=10
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda _:items)
    source=dict(bytes=data,file_name='测试成员-中方课表.pdf',kind='中方')
    blocks=sc._parse_chinese_full_term_visual_occupancy(source,source['file_name'])
    assert not any(b['week']==10 and b['weekday']=='周一' and b['period']==3 for b in blocks)
    notes=sc.visual_notice_consensus_evidence(source)
    assert any(n['week']==10 and n['occupancy_changed'] for n in notes)
