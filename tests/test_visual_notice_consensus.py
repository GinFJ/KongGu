"""Pure, anonymous safeguards for same-page visual notice evidence."""
import copy

import cv2
import numpy as np
import pytest


def _glyph(text='AB'):
    ink=np.zeros((32,64),dtype='uint8')
    cv2.putText(ink,text,(4,25),cv2.FONT_HERSHEY_SIMPLEX,.8,255,1)
    return ink


def _rings(open_gap=False, bridge=False):
    ink=np.zeros((32,64),dtype='uint8')
    cv2.rectangle(ink,(3,4),(29,27),255,1)
    cv2.rectangle(ink,(33,4),(59,27),255,1)
    if open_gap:
        ink[15,29]=0
    if bridge:
        cv2.line(ink,(29,15),(33,15),255,1)
    return ink


def _region(x,y,texts,*,ink=None,page=0,course=False,known_label=None):
    return dict(bbox=(x,y,x+80,y+40),ink=_glyph() if ink is None else ink,
                texts=list(texts),page=page,has_course_code=course,
                known_label=known_label)


def _evidence(regions):
    from core.visual_notice_consensus import match_repeated_notices
    return match_repeated_notices(regions)


def _three(label='教研'):
    return [_region(0,0,[label]),_region(0,60,[label]),
            _region(120,180,['未识别'])]


@pytest.mark.parametrize('label',['教研','不排课'])
def test_two_strict_regions_provide_reviewable_evidence_without_mutating_ocr(label):
    regions=_three(label)
    before=copy.deepcopy(regions)
    result=_evidence(regions)
    assert len(result)==1
    item=result[0]
    assert item['bbox']==regions[2]['bbox']
    assert item['label']==label and item['requires_review'] is True
    assert len(item['support_bboxes'])==2 and min(item['scores'])>=.98
    assert set(item['support_bboxes'])=={regions[0]['bbox'],regions[1]['bbox']}
    for old,new in zip(before,regions):
        assert old['texts']==new['texts']
        assert np.array_equal(old['ink'],new['ink'])


@pytest.mark.parametrize('scale',[.8,1,1.25])
def test_translation_and_uniform_size_changes_preserve_evidence(scale):
    regions=_three()
    for region in regions:
        region['bbox']=tuple(v*scale+93 for v in region['bbox'])
        region['ink']=cv2.resize(region['ink'],None,fx=scale,fy=scale)
    assert len(_evidence(regions))==1


def test_blank_padding_around_glyph_is_not_semantic_content():
    regions=_three()
    regions[2]['ink']=np.pad(regions[2]['ink'],((5,7),(13,9)))
    assert len(_evidence(regions))==1


def test_one_seed_or_duplicate_same_bbox_cannot_make_two_votes():
    regions=_three()
    assert _evidence([regions[0],regions[2]])==[]
    assert _evidence([regions[0],copy.deepcopy(regions[0]),regions[2]])==[]


def test_overlapping_regions_cannot_make_two_votes():
    regions=_three()
    regions[1]['bbox']=(1,1,81,41)
    assert _evidence(regions)==[]


@pytest.mark.parametrize('reverse',[False,True])
def test_duplicate_bbox_with_conflicting_glyph_ratio_is_rejected_in_any_order(reverse):
    regions=_three()
    duplicate=copy.deepcopy(regions[0])
    duplicate['ink']=np.repeat(duplicate['ink'],2,axis=1)
    duplicates=[regions[0],duplicate]
    if reverse:
        duplicates.reverse()
    assert _evidence([*duplicates,regions[1],regions[2]])==[]


def test_binary_zero_one_ink_has_the_same_meaning_as_zero_255():
    regions=_three()
    expected=_evidence(regions)
    for region in regions:
        region['ink']=(region['ink']>0).astype('uint8')
    assert _evidence(regions)==expected


def test_seeds_from_different_pages_cannot_be_combined():
    regions=_three()
    regions[1]['page']=1
    assert _evidence(regions)==[]


def test_inferred_candidates_never_become_support_for_another_candidate():
    regions=_three()
    regions[1]['texts']=['未知甲']
    regions.append(_region(120,240,['未知乙']))
    assert _evidence(regions)==[]


@pytest.mark.parametrize('index',[0,2])
def test_explicit_course_code_conflict_vetoes_seed_and_candidate(index):
    regions=_three()
    regions[index]['has_course_code']=True
    assert _evidence(regions)==[]


@pytest.mark.parametrize('index',[0,2])
@pytest.mark.parametrize('code',['5L33','ACS','123'])
def test_ascii_or_digit_ocr_does_not_override_a_course_even_if_flag_is_missing(index,code):
    regions=_three()
    regions[index]['texts'].append(code)
    assert _evidence(regions)==[]


@pytest.mark.parametrize('code',['ＡＣＳ','１２３','５Ｌ３３'])
def test_fullwidth_course_code_is_a_veto_without_rewriting_raw_text(code):
    regions=_three()
    regions[2]['texts']=[code]
    assert _evidence(regions)==[]
    assert regions[2]['texts']==[code]


def test_seed_with_extra_chinese_title_is_not_strict_evidence():
    regions=_three()
    regions[0]['texts'].append('数学研究方法')
    assert _evidence(regions)==[]


def test_candidate_extra_chinese_title_cannot_be_erased_by_a_similar_patch():
    regions=_three()
    regions[2]['texts']=['被研','数学研究方法']
    assert _evidence(regions)==[]


@pytest.mark.parametrize('title',['数学研究方法','研究生考试辅导'])
def test_long_course_title_is_not_treated_as_a_short_unknown_notice(title):
    regions=_three()
    regions[2]['texts']=[title]
    assert _evidence(regions)==[]


def test_other_known_notice_conflicts_are_not_relabelled():
    regions=_three()
    regions[2]['known_label']='元旦节'
    assert _evidence(regions)==[]
    regions[2]['known_label']=None
    regions[2]['texts']=['不排课']
    assert _evidence(regions)==[]


def test_known_label_metadata_cannot_turn_unknown_ocr_into_a_seed():
    regions=_three()
    regions[1]['texts']=['未知']
    regions[1]['known_label']='教研'
    assert _evidence(regions)==[]


def test_conflicting_known_label_invalidates_a_seed():
    regions=_three()
    regions[0]['known_label']='不排课'
    assert _evidence(regions)==[]


def test_ambiguous_two_label_support_fails_closed():
    regions=_three()
    regions.extend([_region(240,0,['不排课']),_region(240,60,['不排课'])])
    assert _evidence(regions)==[]


def test_different_glyph_does_not_match():
    regions=_three()
    regions[2]['ink']=_glyph('XY')
    assert _evidence(regions)==[]


def test_same_glyph_in_incompatible_merged_geometry_is_not_relabelled():
    regions=_three()
    regions[2]['bbox']=(120,180,360,220)
    assert _evidence(regions)==[]


def test_closing_a_one_pixel_gap_changes_meaningful_loop_topology():
    regions=_three()
    regions[0]['ink']=_rings(open_gap=True)
    regions[1]['ink']=_rings(open_gap=True)
    regions[2]['ink']=_rings()
    assert _evidence(regions)==[]


def test_thin_bridge_between_glyph_components_is_not_ignored():
    regions=_three()
    regions[0]['ink']=_rings()
    regions[1]['ink']=_rings()
    regions[2]['ink']=_rings(bridge=True)
    assert _evidence(regions)==[]


@pytest.mark.parametrize('ink',[np.zeros((20,30),dtype='uint8'),
                               np.full((20,30),255,dtype='uint8'),
                               np.zeros((0,0),dtype='uint8')])
def test_empty_or_solid_pixels_are_not_notice_evidence(ink):
    regions=_three()
    regions[2]['ink']=ink
    assert _evidence(regions)==[]
