import cv2
import numpy as np
import pytest

from core import schedule_core as sc


@pytest.mark.parametrize('scale', [.8, 1, 1.25])
def test_small_visible_glyph_does_not_disappear_when_cell_is_scaled(scale):
    mask = np.zeros((32,32),dtype='uint8')
    mask[10:15,10:19] = 255
    mask = cv2.resize(mask,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
    assert sc._visual_cell_has_occupancy(mask,0,0,mask.shape[1],mask.shape[0],[])


@pytest.mark.parametrize('scale', [.8, 1, 1.25])
def test_isolated_speck_does_not_become_a_course_when_cell_is_scaled(scale):
    mask = np.zeros((32,32),dtype='uint8')
    mask[10:12,10:12] = 255
    mask = cv2.resize(mask,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST)
    assert not sc._visual_cell_has_occupancy(mask,0,0,mask.shape[1],mask.shape[0],[])


@pytest.mark.parametrize('width', [16,32])
def test_taller_row_with_unchanged_glyph_does_not_raise_required_ink(width):
    mask=np.zeros((60,width),dtype='uint8')
    mask[20:30,width//2-5:width//2+5]=255
    assert sc._visual_cell_has_occupancy(mask,0,0,width,60,[])


def test_uniform_grid_uses_shared_scale_despite_one_pixel_row_rounding():
    results=[]
    for height in [29,30]:
        mask=np.zeros((height,32),dtype='uint8')
        mask[10:13,10:21]=255
        results.append(sc._visual_cell_has_occupancy(mask,0,0,32,height,[],scale_height=29.2))
    assert results[0] == results[1]


def test_only_near_uniform_body_rows_share_a_scale():
    from core.bordered_timetables import uniform_row_height

    assert uniform_row_height([0,29,59,88]) == pytest.approx(88/3)
    assert uniform_row_height([0,20,60,80]) is None
