"""Anonymous vector-only notice evidence and conservative scene boundaries."""
import copy

import fitz
import numpy as np
import pytest


def _word(page, y, *, extra=None, inner_orientation=None):
    for x in [35, 52]:
        shape = page.new_shape()
        shape.draw_polyline([(x, y), (x+11, y), (x+11, y+4),
                             (x+5, y+4), (x+5, y+15), (x, y+15)])
        if extra == 'contour' and x == 35:
            shape.draw_rect((x+2, y+2, x+2.01, y+2.01))
        shape.finish(color=None, fill=(0,), closePath=True)
        shape.commit()
    if extra == 'rect':
        page.draw_rect((40, y+2, 40.01, y+2.01), color=None, fill=(0,))


def _fixture(*, extra=None, background=False, conflicting_seeds=False):
    doc = fitz.open()
    page = doc.new_page(width=120, height=330 if conflicting_seeds else 210)
    if background:
        page.draw_rect(page.rect, color=None, fill=(1,))
    regions = []
    for index, y in enumerate([20, 80, 140, *([200, 260] if conflicting_seeds else [])]):
        _word(page, y, extra='rect' if index > 2 else extra if index == 2 else None)
        box = (10, y-8, 110, y+30)
        regions.append(dict(bbox=tuple(v*2.5 for v in box),
                            content_bbox=tuple(v*2.5 for v in (13, y-5, 107, y+27)),
                            page=0, texts=['教研' if index < 2 else '未知' if index == 2 else '不排课'],
                            has_course_code=False, known_label=None,
                            ink=np.zeros((2, 2), dtype='uint8')))
    return doc, page, regions


def _match(page, regions):
    from core.vector_notice_consensus import match_vector_notices
    return match_vector_notices(page, regions)


@pytest.mark.parametrize('scale,padding', [(1, 0), (.8, 0), (1.25, 0), (1, 30)])
def test_uniform_transform_provides_source_geometry_without_mutating_raw_inputs(scale, padding):
    doc, page, regions = _fixture()
    target = fitz.open()
    transformed = target.new_page(width=120*scale+2*padding, height=210*scale+2*padding)
    transformed.show_pdf_page(fitz.Rect(padding, padding, 120*scale+padding,
                                      210*scale+padding), doc, 0)
    for region in regions:
        for key in ['bbox', 'content_bbox']:
            region[key] = tuple(v*scale+padding*2.5 for v in region[key])
    before = copy.deepcopy(regions)
    evidence = _match(transformed, regions)
    assert len(evidence) == 1
    assert evidence[0]['bbox'] == regions[2]['bbox']
    assert evidence[0]['label'] == '教研' and evidence[0]['requires_review']
    assert len(evidence[0]['support_bboxes']) == 2
    assert min(evidence[0]['scores']) >= .98
    assert evidence[0]['vector_evidence']['method_version'] == 'same-page-vector-glyph-v1'
    assert evidence[0]['vector_evidence']['render']['height'] == 128
    assert evidence[0]['vector_evidence']['render']['rendering'] == 'original_pdf_clip'
    for raw, old in zip(regions, before):
        assert raw['texts'] == old['texts']
        assert raw['bbox'] == old['bbox']
        assert np.array_equal(raw['ink'], old['ink'])
    assert _match(transformed, list(reversed(regions))) == evidence
    target.close()
    doc.close()


@pytest.mark.parametrize('extra', ['rect', 'contour'])
def test_tiny_vector_contour_is_not_discarded_by_pixel_topology(extra):
    doc, page, regions = _fixture(extra=extra)
    assert _match(page, regions) == []
    doc.close()


def test_vector_topology_does_not_hide_global_ambiguous_raw_seed_labels():
    doc, page, regions = _fixture(conflicting_seeds=True)
    assert _match(page, regions) == []
    doc.close()


def test_disjoint_components_of_a_grid_path_are_proved_individually():
    doc, page, regions = _fixture()
    shape = page.new_shape()
    # Their union bbox contains the word, but neither filled bar intersects it.
    shape.draw_rect((0, 132, 120, 133))
    shape.draw_rect((0, 169, 120, 170))
    shape.finish(color=None, fill=(0,))
    shape.commit()
    assert len(_match(page, regions)) == 1
    doc.close()


@pytest.mark.parametrize('paint', ['white', 'gray', 'crossing', 'internal_rect'])
def test_unknown_overlapping_or_later_paint_refuses_candidate(paint):
    doc, page, regions = _fixture()
    if paint == 'crossing':
        box, color = (0, 145, 60, 146), (0,)
    elif paint == 'internal_rect':
        box, color = (45, 145, 45.01, 145.01), (0,)
    else:
        box, color = (34, 139, 64, 156), (1,) if paint == 'white' else (.7,)
    page.draw_rect(box, color=None, fill=color)
    assert _match(page, regions) == []
    doc.close()


def test_earlier_white_background_is_safe_but_later_cover_is_not():
    doc, page, regions = _fixture(background=True)
    assert len(_match(page, regions)) == 1
    page.draw_rect((13, 135, 107, 167), color=None, fill=(1,))
    assert _match(page, regions) == []
    doc.close()


@pytest.mark.parametrize('clip', ['full', 'full_lines', 'full_even_odd', 'partial', 'triangle'])
def test_only_containing_rectangular_clip_is_safe(clip):
    doc, page, regions = _fixture()
    stream = b'0 0 120 210 re W n\n' if clip == 'full' else (
        b'0 0 m 120 0 l 120 210 l 0 210 l 0 0 l W n\n' if clip == 'full_lines' else
        b'0 0 m 120 0 l 120 210 l 0 210 l 0 0 l W* n\n' if clip == 'full_even_odd' else
        b'0 0 50 210 re W n\n' if clip == 'partial' else b'0 0 m 120 0 l 0 210 l h W n\n')
    contents = b''.join(doc.xref_stream(x) for x in page.get_contents())
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, b'q\n'+stream+contents+b'\nQ')
    page.set_contents(xref)
    assert bool(_match(page, regions)) is clip.startswith('full')
    doc.close()


@pytest.mark.parametrize('hazard', ['words', 'image', 'annot', 'widget', 'rotation', 'crop', 'opacity', 'layer'])
def test_page_scene_eligibility_is_conservative(hazard):
    doc, page, regions = _fixture()
    if hazard == 'words':
        page.insert_text((5, 200), 'text')
    elif hazard == 'image':
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1), False)
        page.insert_image((0, 0, 1, 1), pixmap=pix)
    elif hazard == 'annot':
        page.add_rect_annot((0, 0, 2, 2))
    elif hazard == 'widget':
        widget = fitz.Widget()
        widget.field_name, widget.field_type, widget.rect = 'field', fitz.PDF_WIDGET_TYPE_TEXT, fitz.Rect(0, 0, 2, 2)
        page.add_widget(widget)
    elif hazard == 'rotation':
        page.set_rotation(90)
    elif hazard == 'crop':
        page.set_cropbox((0, 0, 119, 209))
    elif hazard == 'opacity':
        page.draw_rect((30, 139, 60, 156), color=None, fill=(0,), fill_opacity=.5)
    else:
        doc.add_ocg('optional')
    assert _match(page, regions) == []
    doc.close()


@pytest.mark.parametrize('protection', ['course', 'fullwidth', 'title', 'known', 'duplicate', 'one_seed'])
def test_existing_nonclass_metadata_protections_remain_authoritative(protection):
    doc, page, regions = _fixture()
    if protection == 'course':
        regions[2]['has_course_code'] = True
    elif protection == 'fullwidth':
        regions[2]['texts'] = ['ＡＸ５５']
    elif protection == 'title':
        regions[2]['texts'] = ['课程研究方法']
    elif protection == 'known':
        regions[2]['known_label'] = '校外'
    elif protection == 'duplicate':
        duplicate = copy.deepcopy(regions[0])
        duplicate['texts'] = ['不排课']
        regions.append(duplicate)
    else:
        regions[1]['texts'] = ['未知']
    assert _match(page, regions) == []
    doc.close()


def _nested_rects(page, y, orientation, even_odd=False):
    # Same winding fills the centre; reversed inner winding leaves a hole.
    stream = f'q 0 g 35 {210-y-16} 28 16 re '.encode()
    if orientation == 1:
        stream += f'41 {210-y-12} 16 8 re '.encode()
    else:
        stream += f'41 {210-y-12} m 41 {210-y-4} l 57 {210-y-4} l 57 {210-y-12} l h '.encode()
    stream += b'f* Q' if even_odd else b'f Q'
    xref = page.parent.get_new_xref()
    page.parent.update_object(xref, '<<>>')
    page.parent.update_stream(xref, stream)
    page.set_contents(xref)


def test_nonzero_nested_rect_orientation_is_preserved():
    from core.vector_notice_consensus import _render_paths
    masks = []
    for orientation in [1, -1]:
        doc = fitz.open()
        page = doc.new_page(width=120, height=210)
        _nested_rects(page, 20, orientation)
        mask, _ = _render_paths(page, page.get_drawings())
        masks.append(mask)
        assert bool(mask[64, mask.shape[1]//2]) is (orientation == 1)
        doc.close()
    assert not np.array_equal(*masks)


def test_even_odd_fill_and_reversed_nonzero_contour_preserve_the_same_hole():
    from core.vector_notice_consensus import _render_paths
    masks = []
    for orientation, even_odd in [(1, True), (-1, False)]:
        doc = fitz.open()
        page = doc.new_page(width=120, height=210)
        _nested_rects(page, 20, orientation, even_odd)
        mask, _ = _render_paths(page, page.get_drawings())
        assert mask[64, mask.shape[1]//2] == 0
        masks.append(mask)
        doc.close()
    assert np.array_equal(*masks)


def test_cubic_paths_are_redrawn_without_flattening_or_reordering():
    doc = fitz.open()
    page = doc.new_page(width=120, height=210)
    regions = []
    for index, y in enumerate([20, 80, 140]):
        for x in [40, 60]:
            page.draw_circle((x, y+8), 6, color=None, fill=(0,))
        regions.append(dict(bbox=(25, (y-8)*2.5, 275, (y+30)*2.5),
                            content_bbox=(32.5, (y-5)*2.5, 267.5, (y+27)*2.5),
                            page=0, texts=['教研' if index < 2 else '未知'],
                            has_course_code=False, known_label=None))
    assert len(_match(page, regions)) == 1
    doc.close()


@pytest.mark.parametrize('state,allowed', [
    ('/BM /Screen', False), ('/BM /Normal', True),
    ('/SMask /None', True), ('/SMask mask', False),
    ('/TR function', False), ('/TR /Identity', True), ('/TR2 /Default', True),
])
def test_nonidentity_graphics_state_cannot_be_silently_lost_in_local_redraw(state, allowed):
    doc, page, regions = _fixture()
    if state == '/SMask mask':
        form = doc.get_new_xref()
        doc.update_object(form, '<< /Type /XObject /Subtype /Form /BBox [0 0 120 210] '
                          '/Group << /S /Transparency /CS /DeviceGray >> /Resources <<>> >>')
        doc.update_stream(form, b'0 g 0 0 120 210 re f')
        state = f'/SMask << /S /Luminosity /G {form} 0 R >>'
    elif state == '/TR function':
        function = doc.get_new_xref()
        doc.update_object(function, '<< /FunctionType 2 /Domain [0 1] /C0 [1] /C1 [0] /N 1 >>')
        state = f'/TR {function} 0 R'
    graphics = doc.get_new_xref()
    doc.update_object(graphics, '<< /Type /ExtGState '+state+' >>')
    resources = int(doc.xref_get_key(page.xref, 'Resources')[1].split()[0])
    doc.xref_set_key(resources, 'ExtGState', f'<< /NoticeState {graphics} 0 R >>')
    contents = page.get_contents()[0]
    doc.update_stream(contents, b'/NoticeState gs\n'+doc.xref_stream(contents))
    assert bool(_match(page, regions)) is allowed
    doc.close()


@pytest.mark.parametrize('height', [0, .00001, .001])
def test_extremely_thin_vector_path_is_refused_before_canvas_allocation(monkeypatch, height):
    from core.vector_notice_consensus import _render_paths
    doc = fitz.open()
    page = doc.new_page(width=120, height=210)
    page.draw_rect((10, 10, 20, 10+height), color=None, fill=(0,))
    paths = page.get_drawings()
    def no_allocation(*args, **kwargs):
        raise AssertionError('oversized glyph must be rejected before allocating a page')
    monkeypatch.setattr(fitz, 'open', no_allocation)
    with pytest.raises(ValueError, match='size'):
        _render_paths(page, paths)
    doc.close()


def test_rotated_rectangle_clip_cannot_be_replaced_with_its_axis_aligned_bbox():
    doc, page, regions = _fixture()
    streams = page.get_contents()
    original_seeds = b''.join(doc.xref_stream(x) for x in streams[:4])
    candidate = b''.join(doc.xref_stream(x) for x in streams[4:])
    # The diamond's bbox contains content_bbox, but the real polygon cuts the
    # candidate glyphs. MuPDF emits a rectangular (rotated) qu for this clip.
    clip = b'q 60 75 m 10 25 l 60 -25 l 110 25 l 60 75 l W n\n'
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, original_seeds+clip+candidate+b'\nQ')
    page.set_contents(xref)
    clips = [path for path in page.get_drawings(extended=True) if path['type'] == 'clip']
    assert clips[0]['items'][0][0] == 'qu'
    assert clips[0]['items'][0][1].is_rectangular
    assert _match(page, regions) == []
    doc.close()


@pytest.mark.parametrize('protection', ['course', 'title', 'code'])
def test_obvious_course_metadata_is_skipped_before_copying_a_pdf(monkeypatch, protection):
    from core import vector_notice_consensus as vector
    doc, page, regions = _fixture()
    for region in regions:
        if protection == 'course':
            region['has_course_code'] = True
        else:
            region['texts'] = ['课程研究方法' if protection == 'title' else 'ＡＸ５５']
    def no_render(*args, **kwargs):
        raise AssertionError('clear course metadata must be refused before PDF copying')
    monkeypatch.setattr(vector, '_render_paths', no_render)
    assert _match(page, regions) == []
    doc.close()


def test_original_moveto_subpaths_are_not_joined_when_drawing_items_lose_the_move():
    from core.vector_notice_consensus import _render_paths
    doc = fitz.open()
    page = doc.new_page(width=120, height=210)
    regions, commands, original_masks = [], [], []
    for index, y in enumerate([20, 80, 140]):
        for x in [35, 52]:
            points = [(x, y), (x+11, y), (x+5, y+7), (x+11, y+15), (x, y+15)]
            def point(number):
                px, py = points[number]
                return f'{px} {210-py}'
            # The candidate closes two open triangles at a new move at C.
            # get_drawings omits this move and reports the same four lines as
            # the seeds' single concave pentagon, although their fills differ.
            move = f' {point(2)} m' if index == 2 else ''
            commands.append(f'0 g {point(0)} m {point(1)} l {point(2)} l'
                            f'{move} {point(3)} l {point(4)} l f')
        regions.append(dict(bbox=(25, (y-8)*2.5, 275, (y+30)*2.5),
                            content_bbox=(32.5, (y-5)*2.5, 267.5, (y+27)*2.5),
                            page=0, texts=['教研' if index < 2 else '未知'],
                            has_course_code=False, known_label=None))
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, '\n'.join(commands).encode())
    page.set_contents(xref)
    assert all([item[0] for item in path['items']] == ['l']*4 for path in page.get_drawings())
    for y in [20, 140]:
        pixmap = page.get_pixmap(clip=fitz.Rect(35, y, 63, y+15), dpi=144, colorspace=fitz.csGRAY)
        original_masks.append(np.frombuffer(pixmap.samples, dtype='uint8') < 200)
    difference = np.count_nonzero(original_masks[0] != original_masks[1])
    union = np.count_nonzero(original_masks[0] | original_masks[1])
    assert difference/union > .2
    paths = page.get_drawings()
    normalized = [_render_paths(page, group)[0] > 0 for group in [paths[:2], paths[4:]]]
    normalized_difference = np.count_nonzero(normalized[0] != normalized[1])
    normalized_union = np.count_nonzero(normalized[0] | normalized[1])
    # Source clipping retains the original geometric difference at the fixed
    # normalization size; a lossy command reconstruction makes this ratio zero.
    assert normalized_difference/normalized_union > .2
    assert abs(normalized_difference/normalized_union-difference/union) < .05
    assert _match(page, regions) == []
    doc.close()


def test_match_reuses_one_original_page_displaylist_without_copying_the_pdf(monkeypatch):
    doc, page, regions = _fixture()
    streams = page.get_contents()
    original_streams = [doc.xref_stream(x) for x in streams]
    original_drawings = page.get_drawings(extended=True)
    original_xrefs = doc.xref_length()
    calls = []
    original = fitz.Page.get_displaylist
    def tracked(source, *args, **kwargs):
        calls.append((source.number, kwargs))
        return original(source, *args, **kwargs)
    def no_pdf_copy(*args, **kwargs):
        raise AssertionError('local normalization must not copy the whole source PDF')
    monkeypatch.setattr(fitz.Page, 'get_displaylist', tracked)
    monkeypatch.setattr(fitz.Page, 'show_pdf_page', no_pdf_copy)
    result = _match(page, regions)
    assert len(result) == 1
    assert calls == [(0, {'annots': False})]
    assert result[0]['vector_evidence']['render']['rendering'] == 'original_pdf_clip'
    assert result[0]['vector_evidence']['render']['backend'] == 'display_list'
    assert doc.xref_length() == original_xrefs
    assert page.get_contents() == streams
    assert [doc.xref_stream(x) for x in streams] == original_streams
    assert page.get_drawings(extended=True) == original_drawings
    doc.close()
