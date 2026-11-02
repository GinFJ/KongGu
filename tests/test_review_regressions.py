import json
from core import schedule_core as sc


def test_name_before_punctuation_still_participates_in_identity_check():
    assert sc._extract_chinese_name_from_text('姓名：李四，班级202601') == '李四'
    assert sc._extract_chinese_name_from_text('姓名：欧阳明月（学生）') == '欧阳明月'
    assert sc._extract_chinese_name_from_text('姓名张三性别男') == '张三'


def test_layout_cache_rejects_metadata_for_another_source(monkeypatch, tmp_path):
    source={'file_name':'成员甲-中方课表.pdf','kind':'中方','bytes':b'%PDF-anonymous-test'}
    monkeypatch.setenv('KONGGU_OCR_LAYOUT_CACHE',str(tmp_path))
    cache=sc._ocr_layout_cache_path(source)
    cache.write_text(json.dumps({'version':sc.OCR_LAYOUT_CACHE_VERSION,
        'ocr_signature':sc._ocr_cache_signature(),'hash':'f'*64,
        'items':[{'text':'wrong-source','page':0,'x0':1,'y0':1}]}),encoding='utf-8')
    attempts=[]
    monkeypatch.setattr(sc,'_open_pdf_document',lambda src: attempts.append(True))
    assert sc._extract_pdf_ocr_items(source)==[]
    assert attempts==[True]


def test_visible_header_alignment_does_not_remove_nearby_course(monkeypatch):
    from core import bordered_timetables as bt
    import numpy as np
    def item(text,x,y):return dict(text=text,x0=x,x1=x+10,y0=y,y1=y+10,page=0)
    headers=[item(str(p),100+p*30,95) for p in range(1,12)]
    course=item('ACS',250,113)
    monkeypatch.setattr(sc,'_extract_pdf_ocr_items',lambda *a,**k:[course])
    class Doc:
        def __getitem__(self,index):return object()
        def close(self):pass
    monkeypatch.setattr(sc,'_open_pdf_document',lambda source:Doc())
    monkeypatch.setattr(sc,'_page_image',lambda *a:np.zeros((200,600,3),dtype='uint8'))
    monkeypatch.setattr(bt,'english_header_items',lambda gray:headers)
    seen=[]
    def parse(items,name):
        seen.extend(items)
        return [{'date':'2026-03-02','period':1}]
    monkeypatch.setattr(sc,'_parse_english_grid_page_items_from_pdf_words',parse)
    monkeypatch.setattr(sc,'_english_ocr_merged_cell_supplements',lambda *a:[])
    sc._parse_english_ocr_week_grid({},'成员甲-英方课表.pdf')
    assert any(i['text']=='ACS' for i in seen)
