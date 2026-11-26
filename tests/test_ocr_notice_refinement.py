"""A second reading may resolve a calendar notice only with matching evidence."""
import json

import fitz
import pytest

from core import schedule_core as sc


def cached_notice(tmp_path, monkeypatch, *, wide=True):
    monkeypatch.setenv('KONGGU_OCR_LAYOUT_CACHE',str(tmp_path))
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=300)
        page.draw_rect(fitz.Rect(10 if wide else 290,20,590 if wide else 350,50),color=(0,0,0))
        page.insert_text((300,40),'考试周',fontname='china-s',fontsize=10)
        source=dict(bytes=doc.tobytes(),file_name='测试-中方课表.pdf',kind='中方')
    item=dict(text='考试围',x0=500,y0=48,x1=550,y1=69,page=0,
              page_width=1000,page_height=500,confidence=.75)
    cache=sc._ocr_layout_cache_path(source)
    cache.write_text(json.dumps(dict(version=sc.OCR_LAYOUT_CACHE_VERSION,
        ocr_signature=sc._ocr_cache_signature(),hash=sc._source_digest(source),items=[item])),encoding='utf-8')
    return source,cache,item


def fake_engine(monkeypatch, texts):
    calls=[]
    class Engine:
        def ocr(self,image):
            text=texts[min(len(calls),len(texts)-1)]
            calls.append(image.shape)
            return dict(rec_texts=[text],rec_scores=[.99],rec_boxes=[[0,0,50,20]])
    monkeypatch.setattr(sc,'_build_ocr_engine',lambda:Engine())
    return calls


def test_local_notice_reread_requires_two_native_renders_and_keeps_raw_cache(tmp_path,monkeypatch):
    source,cache,original=cached_notice(tmp_path,monkeypatch)
    before=cache.read_bytes()
    calls=fake_engine(monkeypatch,['考试周','考试周'])
    result=sc._extract_pdf_ocr_items(source)
    assert result[0]['text']=='考试周'
    assert len(calls)==2
    assert calls[0]!=calls[1]
    assert result[0]['original_text']==original['text']
    assert cache.read_bytes()==before


@pytest.mark.parametrize('wide,texts',[(False,['考试周','考试周']),(True,['考试周','考试课'])])
def test_notice_reread_never_relabels_a_course_or_disagreement(tmp_path,monkeypatch,wide,texts):
    source,cache,original=cached_notice(tmp_path,monkeypatch,wide=wide)
    calls=fake_engine(monkeypatch,texts)
    result=sc._extract_pdf_ocr_items(source)
    assert result==[original]
    if not wide:assert calls==[]


@pytest.mark.parametrize('bad', ['微', '我留', 'RE'])
def test_short_notice_requires_column_support_and_native_agreement(tmp_path,monkeypatch,bad):
    from core.ocr_notice_refinement import refine_notice_items
    doc=fitz.open();page=doc.new_page(width=600,height=300)
    items=[]
    for index in range(4):
        top=20+index*30
        page.draw_line((10,top),(590,top),color=(0,0,0))
        page.draw_rect(fitz.Rect(290,top,350,top+30),color=(0,0,0))
        page.insert_text((300,top+20),'教研',fontname='china-s',fontsize=10)
        items.append(dict(text=bad if index==2 else '教研',page=0,
                          x0=500,x1=550,y0=(top+10)/.6,y1=(top+25)/.6,
                          confidence=.3 if index==2 else .99))
    page.draw_line((10,140),(590,140),color=(0,0,0))
    source={'bytes':doc.tobytes()};doc.close()
    calls=fake_engine(monkeypatch,['教研','教研'])
    result=refine_notice_items(source,items,sc)
    assert result[2]['text']=='教研'
    assert items[2]['text']==bad
    assert len(calls)==2
    # The same isolated word without repeated-column evidence is unresolved.
    source.pop('_notice_refinement_cache',None)
    assert refine_notice_items(source,[items[2]],sc)==[items[2]]


@pytest.mark.parametrize('bad,confidence',[('午节',.99),('劳陆节',.8)])
def test_partial_or_confident_holiday_at_open_table_end(monkeypatch,bad,confidence):
    from core.ocr_notice_refinement import refine_notice_items
    doc=fitz.open();page=doc.new_page(width=600,height=300)
    for y in [20,50]:page.draw_line((10,y),(590,y),color=(0,0,0))
    page.draw_line((400,20),(400,50),color=(0,0,0))
    label='端午节' if bad=='午节' else '劳动节'
    page.insert_text((460,40),label,fontname='china-s',fontsize=10)
    item=dict(text=bad,page=0,x0=460/.6,x1=490/.6,y0=30/.6,y1=43/.6,confidence=confidence)
    source={'bytes':doc.tobytes()};doc.close()
    calls=fake_engine(monkeypatch,[label,label])
    result=refine_notice_items(source,[item],sc)
    assert result[0]['text']==label
    assert len(calls)==2
    assert item['text']==bad
