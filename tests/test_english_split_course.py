"""The OCR route can read a word split by the physical print-page edge."""
import cv2
import fitz
import numpy as np
import pytest

from core import schedule_core as sc
from tests.test_english_cell_completeness import grid_page


def fixture(monkeypatch, *, adjacent=True, aligned=True, agree=True, pale=False, unseen=False):
    second, words = grid_page()
    # The continuation sits at the top of the second sheet. Its first course
    # is already detected as occupied, but the code itself was cut in half.
    first = np.full_like(second, 255)
    xs = [20,120,170,220,230,280,330,510,560,610,630,680,730,800,850,900,950]
    shift = 0 if aligned else 25
    for x in xs:
        cv2.line(first,(x+shift,480),(x+shift,585),(0,0,0),1)
    for y in [480,510,570]:
        cv2.line(first,(20+shift,y),(950+shift,y),(210,210,210) if pale and y==570 else (0,0,0),1)
    first[571:586,679:682]=255  # merged 7/8 cell, as on the next page
    first[511:570,679:682]=255
    # Visible cut-off ink; the local recognizer is deterministic in this test.
    cv2.putText(first,'ABC',(655,585),cv2.FONT_HERSHEY_SIMPLEX,.3,(0,0,0),1)
    previous = [dict(text='Monday',x0=25,y0=528,x1=100,y1=538,page=0),
                dict(text='2026-11-09' if adjacent else '2026-11-02',
                     x0=25,y0=541,x1=110,y1=551,page=0)]
    for y,text in [(515,'ABC'),(530,'L3B-7'),(545,'8401')]:
        previous.append(dict(text=text,x0=650,x1=710,y0=y,y1=y+10,page=0))
        cv2.putText(first,text,(650,y+8),cv2.FONT_HERSHEY_SIMPLEX,.3,(0,0,0),1)
    for p,col in enumerate([1,2,4,5,7,8,10,11,13,14,15],1):
        previous.append(dict(text=str(p),x0=(xs[col]+xs[col+1])/2-4,
                             x1=(xs[col]+xs[col+1])/2+4,y0=490,y1=500,page=0))
    for word in words:word['page']=1
    all_words=previous+words
    for word in all_words:word.update(page_width=1100,page_height=600)
    doc=fitz.open()
    for image in [first,second]:
        page=doc.new_page(width=660,height=360)
        page.insert_image(page.rect,stream=cv2.imencode('.png',image)[1].tobytes())
    source={'bytes':doc.tobytes()};doc.close()
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:all_words)
    calls=[]
    class Engine:
        def ocr(self,image):
            calls.append(image.shape)
            label='ABF' if unseen else ('ABC' if agree or len(calls)%2 else 'XYZ')
            return dict(rec_texts=[label,'L3B-7','8401'],rec_scores=[.99]*3,
                        rec_boxes=[[10,10,60,25],[10,30,70,45],[10,50,70,65]])
    monkeypatch.setattr(sc,'_build_ocr_engine',lambda:Engine())
    return source,calls


@pytest.mark.parametrize('pale',[False,True])
def test_split_course_is_recovered_on_production_ocr_route(monkeypatch,pale):
    source,calls=fixture(monkeypatch,pale=pale)
    blocks=sc._parse_english_ocr_week_grid(source,'示例甲-英方课表.pdf')
    target=[b for b in blocks if b['date']=='2026-11-10']
    assert {b['period'] for b in target}=={7,8}
    assert {b['course'] for b in target}=={'ABC'}
    assert all(b['provenance'].get('split_course_evidence') for b in target)
    assert len(calls)==2


@pytest.mark.parametrize('kwargs',[{'adjacent':False},{'aligned':False},{'agree':False},{'unseen':True}])
def test_unsafe_or_disagreeing_fragments_stay_occupied_and_pending(monkeypatch,kwargs):
    source,calls=fixture(monkeypatch,**kwargs)
    blocks=sc._parse_english_ocr_week_grid(source,'示例甲-英方课表.pdf')
    target=[b for b in blocks if b['date']=='2026-11-10']
    assert {b['period'] for b in target}=={7,8}
    assert all(b['provenance']['cell_text_incomplete'] for b in target)
