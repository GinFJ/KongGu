"""Source-bound review evidence survives caches; raw OCR stays unchanged."""
import copy
from pathlib import Path

import cv2
import numpy as np
import pytest

from core import schedule_core as sc
from core.bordered_timetables import parse_chinese_bordered_image


def test_english_source_problem_survives_cache_and_rejects_stale_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('KONGGU_PARSE_CACHE', str(tmp_path))
    source = dict(bytes=b'anonymous-source', file_name='测试-英方课表.pdf', kind='英方')
    note = dict(kind='overlay', source_hash=sc._source_digest(source),
                parser_signature=sc.cache_signature(), ocr_signature=sc._ocr_cache_signature(), page=0)
    source['_english_grid_evidence'] = [note]
    sc._write_parse_cache(source, '英方', [dict(week=1)])
    fresh = {k:v for k,v in source.items() if not k.startswith('_')}
    assert sc.english_grid_evidence(fresh)==[note]
    assert sc.english_grid_evidence({**fresh,'bytes':b'changed'})==[]
    monkeypatch.setattr(sc,'cache_signature',lambda:'changed')
    assert sc.english_grid_evidence(fresh)==[]


def test_optional_cache_write_failure_keeps_source_evidence_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv('KONGGU_PARSE_CACHE', str(tmp_path))
    source = dict(bytes=b'anonymous-source', file_name='测试-中方课表.pdf', kind='中方')
    note = dict(source_hash=sc._source_digest(source), parser_signature=sc.cache_signature(),
                ocr_signature=sc._ocr_cache_signature(), canonical_label='教研')
    source['_visual_notice_evidence'] = [note]
    def unavailable(*args, **kwargs):
        raise OSError('cache is unavailable')
    monkeypatch.setattr(Path, 'write_text', unavailable)
    sc._write_parse_cache(source, '中方', [dict(week=1)])
    assert sc.visual_notice_consensus_evidence(source) == [note]


def test_cache_keeps_notice_evidence_and_rejects_changed_source_or_signature(tmp_path, monkeypatch):
    monkeypatch.setenv('KONGGU_PARSE_CACHE', str(tmp_path))
    source = dict(bytes=b'anonymous-source', file_name='测试-中方课表.pdf', kind='中方')
    note = dict(kind='same_page_nonclass_consensus', source_hash=sc._source_digest(source),
                parser_signature=sc.cache_signature(), ocr_signature=sc._ocr_cache_signature(),
                page=0, bbox=[1, 2, 3, 4], original_ocr=[dict(text='未知')],
                canonical_label='教研', seeds=[dict(bbox=[5,2,7,4]),dict(bbox=[8,2,10,4])])
    source['_visual_notice_evidence'] = [note]
    sc._write_parse_cache(source, '中方', [dict(week=1)])
    fresh = {k:v for k,v in source.items() if not k.startswith('_')}
    assert sc._load_parse_cache(fresh, '中方') == [dict(week=1)]
    evidence = sc.visual_notice_consensus_evidence(fresh)
    assert evidence == [note]
    evidence[0]['original_ocr'][0]['text'] = 'altered'
    assert sc.visual_notice_consensus_evidence(fresh) == [note]
    assert sc.visual_notice_consensus_evidence({**fresh, 'bytes':b'changed-source'}) == []
    old_ocr_signature = sc._ocr_cache_signature()
    monkeypatch.setattr(sc, '_ocr_cache_signature', lambda: 'changed-ocr')
    assert sc._load_parse_cache(fresh, '中方') is None
    assert sc.visual_notice_consensus_evidence(fresh) == []
    monkeypatch.setattr(sc, '_ocr_cache_signature', lambda: old_ocr_signature)
    monkeypatch.setattr(sc, 'cache_signature', lambda: 'changed-parser')
    assert sc.visual_notice_consensus_evidence(fresh) == []


@pytest.mark.parametrize('conflict', ['', 'course_code', 'crossing_box'])
def test_physical_region_suggestion_preserves_all_occupancy_and_raw_boxes(conflict, monkeypatch):
    image = np.full((900, 1400), 255, dtype=np.uint8)
    xs = [20, 60]
    for _ in range(7):
        for width in [12,48,10,24,36,48]:
            xs.append(xs[-1]+width)
    for y in [40,55,*range(70,491,21),550]:
        cv2.line(image,(20,y),(xs[-1],y),0,1)
    for x in xs:
        cv2.line(image,(x,55),(x,490),0,1)
    items = [dict(text=f'{week}周',x0=22,x1=45,
                  y0=70+(week-1)*21+4,y1=70+(week-1)*21+12)
             for week in [1,10,20]]
    for week, text in [(2,'教研'),(3,'教研'),(10,'未知')]:
        left,right=xs[2],xs[3]
        top=70+(week-1)*21
        cv2.putText(image,'AB',(left+5,top+15),cv2.FONT_HERSHEY_SIMPLEX,.4,0,1)
        items.append(dict(text=text,x0=left+4,x1=left+24,y0=top+3,y1=top+17))
        if conflict == 'crossing_box' and week == 10:
            items[-1]['x0'] = left-5
        if conflict == 'course_code' and week == 10:
            items.append(dict(text='5L33',x0=left+25,x1=right-1,y0=top+3,y1=top+17))
    # A real, independent neighbouring course must survive inference.
    top=70+9*21
    cv2.putText(image,'XYZ',(xs[5]+2,top+15),cv2.FONT_HERSHEY_SIMPLEX,.4,0,1)
    original=copy.deepcopy(items)
    notes=[]
    blocks=parse_chinese_bordered_image(image,'测试成员',items,evidence_out=notes)
    occupied={(b['week'],b['weekday'],b['period']) for b in blocks}
    assert items == original
    assert (10,'周一',7) in occupied
    assert (10,'周一',3) in occupied
    assert len(notes) == (0 if conflict else 1)
    if notes:
        note=notes[0]
        assert note['original_ocr'][0]['text'] == '未知'
        assert len(note['seeds']) == 2 and note['requires_review']
        assert note['week']==10 and note['periods']==[3,4]
        assert note['action']=='suggested_nonclass_requires_review'
        assert note['occupancy_changed'] is False
        assert all(seed['original_ocr'][0]['text']=='教研' for seed in note['seeds'])
    from core import visual_notice_consensus
    monkeypatch.setattr(visual_notice_consensus, 'match_repeated_notices', lambda _: [])
    without_suggestions=parse_chinese_bordered_image(image,'测试成员',items)
    assert occupied == {(b['week'],b['weekday'],b['period']) for b in without_suggestions}
