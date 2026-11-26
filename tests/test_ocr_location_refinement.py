import fitz
import pytest

from core import schedule_core as sc
from core.ocr_location_refinement import refine_missing_locations


def sample():
    doc=fitz.open();p=doc.new_page(width=600,height=300)
    for y in [20,50,80]:p.draw_line((10,y),(590,y),color=(0,0,0))
    for y in [20,50]:
        p.draw_rect(fitz.Rect(290,y,350,y+30),color=(0,0,0))
        p.insert_text((300,y+12),'示R42',fontname='china-s',fontsize=8)
        p.insert_text((300,y+24),'校外001',fontname='china-s',fontsize=8)
    source={'bytes':doc.tobytes()};doc.close()
    item=dict(text='示R42',page=0,x0=300/.6,x1=330/.6,y0=53/.6,y1=65/.6,
              page_width=1000,page_height=500,confidence=.99)
    return source,[dict(item,text='校外002',y0=33/.6,y1=45/.6),item]


@pytest.mark.parametrize('mode',['agree','disagree','low_confidence','different_course'])
def test_missing_room_requires_two_matching_reads_of_its_own_cell(monkeypatch,mode):
    source,items=sample();calls=[]
    class Engine:
        def ocr(self,image):
            calls.append(image.shape)
            return dict(rec_texts=['示R99' if mode=='different_course' else '示R42',
                       '校外003' if mode=='disagree' and len(calls)==2 else '校外001'],
                       rec_scores=[.99,.5 if mode=='low_confidence' else .99],
                       rec_boxes=[[5,5,40,15],[5,20,50,30]])
    monkeypatch.setattr(sc,'_build_ocr_engine',lambda:Engine())
    result=refine_missing_locations(source,items,sc)
    if mode=='agree':
        assert result[-1]['text']=='校外001'  # never copy the other cell's 002
        assert len(calls)==2 and calls[0]!=calls[1]
        assert source['_visual_notice_evidence'][0]['kind']=='native_crop_offcampus'
        assert not source['_visual_notice_evidence'][0]['requires_review']
    else:assert result==items
    assert len(items)==2


def test_existing_room_is_never_overwritten(monkeypatch):
    source,items=sample()
    items.append(dict(items[1],text='E101',y0=68/.6,y1=76/.6))
    monkeypatch.setattr(sc,'_build_ocr_engine',lambda:pytest.fail('existing room must not be reread'))
    assert refine_missing_locations(source,items,sc)==items
