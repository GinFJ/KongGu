"""Conservative same-page notice evidence from original PDF glyph clips.

The source page, physical cells and raw OCR stay unchanged. Page rendering is
not normalized: only complete opaque glyph bounds are clipped at a fixed local
size, after proving that clipping, grid paint and overlays are harmless. The
original PDF retains subpath moves that the drawing-inspection API may omit.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Iterable, Mapping

import fitz
import numpy as np

from . import visual_notice_consensus as notices


_METHOD = 'same-page-vector-glyph-v1'
_HEIGHT, _MARGIN = 128, 2
_STATE_KEYS = re.compile(r'/(BM|SMask|TR2?)(?=[\s/<>\[\]()])\s*')


def _rect(value: Any) -> fitz.Rect | None:
    box = notices._box(value)
    rectangle = fitz.Rect(box) if box is not None else None
    return rectangle if rectangle is not None and not rectangle.is_infinite else None


def _same_point(a: Any, b: Any) -> bool:
    # PDF coordinates are float32. This only reconciles representational
    # roundoff, not visible gaps or a configurable geometric similarity.
    bound = max(1.0, abs(a.x), abs(a.y), abs(b.x), abs(b.y))*np.finfo(np.float32).eps*2
    return abs(a.x-b.x) <= bound and abs(a.y-b.y) <= bound


def _rectangle_path(path: Mapping[str, Any]) -> fitz.Rect | None:
    """Prove a single rectangle, including PDF exporters' four-line clips."""
    items = path.get('items', ())
    if len(items) == 1 and items[0][0] == 're':
        return _rect(items[0][1])
    if len(items) == 1 and items[0][0] == 'qu':
        quad = fitz.Quad(items[0][1])
        box = _rect(quad.rect)
        if box is None or not quad.is_rectangular:
            return None
        # A rotated rectangle's axis bbox is larger than its actual clip.
        # Only axis-aligned vertices establish the containment proof below.
        corners = (box.tl, box.tr, box.bl, box.br)
        return box if all(point in corners for point in quad) else None
    if len(items) != 4 or any(item[0] != 'l' for item in items):
        return None
    points = [item[1] for item in items]
    if any(not _same_point(item[2], items[(i+1) % 4][1]) for i, item in enumerate(items)):
        return None
    box = _rect((min(point.x for point in points), min(point.y for point in points),
                 max(point.x for point in points), max(point.y for point in points)))
    if box is None:
        return None
    corners = [box.tl, box.bl, box.br, box.tr]
    if any(not any(_same_point(point, corner) for corner in corners) for point in points):
        return None
    if len({(point.x, point.y) for point in points}) != 4:
        return None
    if any(item[1].x != item[2].x and item[1].y != item[2].y for item in items):
        return None
    return box


def _identity_graphics_states(document: Any) -> bool:
    # Drawings expose blend groups, but transfer functions may be invisible in
    # that API. Inspect MuPDF's normalized object dictionaries, including inline
    # ExtGState dictionaries. Unused unsupported state also causes fallback;
    # proving resource reachability is deliberately outside this small module.
    allowed = {'BM': 'Normal', 'SMask': 'None', 'TR': 'Identity', 'TR2': 'Identity|Default'}
    for xref in range(1, document.xref_length()):
        value = document.xref_object(xref, compressed=False)
        for match in _STATE_KEYS.finditer(value):
            if not re.match(r'(?:null|/(?:'+allowed[match[1]]+r'))(?=$|[\s/<>\[\]()])', value[match.end():]):
                return False
    return True


def _scene_paths(page: Any) -> list[dict[str, Any]] | None:
    if page.rotation or tuple(page.cropbox) != tuple(page.mediabox):
        return None
    if page.rect.x0 != 0 or page.rect.y0 != 0:
        return None
    if (page.get_text('words') or page.get_image_info()
            or next(page.annots(), None) is not None
            or next(page.widgets(), None) is not None or page.parent.get_ocgs()):
        return None
    if not _identity_graphics_states(page.parent):
        return None
    # Shadings and other paints are absent from get_drawings(). Refuse rather
    # than silently compare a scene that it cannot fully describe.
    if any(kind not in ('fill-path', 'stroke-path') for kind, *_ in page.get_bboxlog()):
        return None
    paths, clips = [], {}
    for raw in page.get_drawings(extended=True):
        level = raw.get('level', 0)
        clips = {depth: clip for depth, clip in clips.items() if depth < level}
        if raw.get('layer') or raw.get('type') == 'group':
            return None
        if raw.get('type') == 'clip':
            clips[level] = _rectangle_path(raw)
            continue
        if raw.get('type') not in ('f', 's', 'fs'):
            return None
        path = dict(raw)
        path['_clips'] = tuple(clips.values())
        radius = float(path.get('width') or 0)/2 if path['type'] != 'f' else 0
        items = path.get('items', ())
        boxes = [fitz.Rect(item[1]) for item in items] if items and all(item[0] == 're' for item in items) else [fitz.Rect(path['rect'])]
        path['_paint_boxes'] = tuple(tuple(box+(-radius, -radius, radius, radius)) for box in boxes)
        if radius < 0 or not all(math.isfinite(v) for box in path['_paint_boxes'] for v in box):
            return None
        paths.append(path)
    return paths


def _intersects(path: Mapping[str, Any], roi: fitz.Rect) -> bool:
    # A table-grid record can contain several disjoint rectangles whose union
    # bbox crosses the cell. Prove disjointness per primitive in this case.
    return any(box[0] < roi.x1 and box[2] > roi.x0 and box[1] < roi.y1 and box[3] > roi.y0
               for box in path['_paint_boxes'])


def _contours(path: Mapping[str, Any]) -> int | None:
    """Count exposed contours, including those too small for pixel topology.

    This is an additional veto, not proof that get_drawings retains every move.
    Original PDF clipping preserves the actual fill semantics.
    """
    count, start, previous = 0, None, None
    for item in path.get('items', ()):
        kind = item[0]
        if kind == 're':
            if _rect(item[1]) is None or item[2] not in (-1, 1):
                return None
            count += 1
            start = previous = None
        elif kind in ('l', 'c'):
            if len(item) != (3 if kind == 'l' else 5):
                return None
            if not all(math.isfinite(v) for point in item[1:] for v in point):
                return None
            first, last = item[1], item[-1]
            # Connectivity is exact in MuPDF's already-decoded command stream:
            # applying a proximity tolerance here could erase a tiny contour.
            if previous is None or previous == start or previous != first:
                count += 1
                start = first
            previous = last
        else:
            return None
    return count or None


def _collect(paths: list[dict[str, Any]], roi: fitz.Rect) -> list[dict[str, Any]] | None:
    glyphs, backdrops = [], []
    for path in paths:
        if not _intersects(path, roi):
            continue
        if any(clip is None or not clip.contains(roi) for clip in path['_clips']):
            return None
        if path['type'] != 'f' or path.get('fill_opacity') != 1:
            return None
        color = path.get('fill')
        if color and all(channel == 1 for channel in color):
            box = _rectangle_path(path)
            if box is None or not box.contains(roi):
                return None
            backdrops.append(path)
            continue
        if not color or any(channel != 0 for channel in color):
            return None
        box = _rect(path.get('rect'))
        if box is None or not roi.contains(box) or _contours(path) is None:
            return None
        glyphs.append(path)
    if not glyphs or any(background['seqno'] >= min(path['seqno'] for path in glyphs)
                         for background in backdrops):
        return None
    return glyphs


def _render_paths(page: Any, paths: list[Mapping[str, Any]], *, displaylist: Any = None) -> tuple[Any, dict[str, Any]]:
    box = fitz.Rect(paths[0]['rect'])
    for path in paths[1:]:
        box |= fitz.Rect(path['rect'])
    if _rect(box) is None:
        raise ValueError('invalid vector glyph size')
    factor = (_HEIGHT-2*_MARGIN)/box.height
    glyph_width = box.width*factor
    width = math.ceil(glyph_width)+2*_MARGIN
    if width > 4096:
        raise ValueError('vector glyph size exceeds canvas limit')
    if displaylist is None:
        displaylist = page.get_displaylist(annots=False)
    matrix = fitz.Matrix(factor, 0, 0, factor,
                         _MARGIN-box.x0*factor, _MARGIN-box.y0*factor)
    pixels = displaylist.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY,
                                   alpha=False, clip=box)
    gray = np.frombuffer(pixels.samples, dtype='uint8').reshape(pixels.height, pixels.width)
    canvas = np.full((_HEIGHT, width), 255, dtype='uint8')
    # The device may round transformed clip bounds outwards by a pixel. Respect
    # its original x/y offset rather than stretching or silently recentring it.
    x0, y0 = max(0, pixels.x), max(0, pixels.y)
    x1, y1 = min(width, pixels.x+pixels.width), min(_HEIGHT, pixels.y+pixels.height)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = gray[y0-pixels.y:y1-pixels.y, x0-pixels.x:x1-pixels.x]
    ink = (canvas < 200).astype('uint8')*255
    return ink, dict(height=_HEIGHT, glyph_height=124, margin=_MARGIN,
                     width=width, dpi=72, gray_threshold=200, glyph_bbox=list(box),
                     glyph_bbox_units='pdf_points', rendering='original_pdf_clip',
                     backend='display_list')


def _metadata_eligible(raw: Mapping[str, Any]) -> bool:
    """Avoid copying a PDF for metadata the existing matcher always vetoes."""
    flag, texts = raw.get('has_course_code'), raw.get('texts')
    if not isinstance(flag, (bool, np.bool_)) or flag:
        return False
    if not isinstance(texts, (list, tuple)) or not all(isinstance(term, str) for term in texts):
        return False
    terms = {term.strip() for term in texts if term.strip()}
    return len(terms) <= 1 and not any(len(term) > 3 or re.search(
        r'[A-Za-z0-9]', unicodedata.normalize('NFKC', term)) for term in terms)


def match_vector_notices(page: Any, regions: Iterable[Mapping[str, Any]], *, dpi: int = 180) -> list[dict[str, Any]]:
    """Return review evidence only for complete, unobscured vector words.

    ``bbox`` and ``content_bbox`` use the original page's ``dpi`` pixel frame.
    The latter must be the physical cell inset supplied by the grid parser,
    never an OCR word box. Raw OCR metadata is passed to the existing matcher.
    """
    if not isinstance(dpi, (int, float)) or not math.isfinite(dpi) or dpi <= 0:
        return []
    try:
        paths = _scene_paths(page)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        return []
    if paths is None:
        return []
    grouped = {}
    for raw in regions:
        if not isinstance(raw, Mapping):
            continue
        box = notices._box(raw.get('bbox'))
        number = raw.get('page', 0)
        if box is not None and isinstance(number, (int, np.integer)) and number >= 0:
            grouped.setdefault((int(number), box), []).append(raw)
    all_rendered, by_key, details = [], {}, {}
    displaylist = None
    for (number, box), duplicates in grouped.items():
        if number != page.number:
            continue
        rendered, fingerprint = [], None
        for raw in duplicates:
            if not _metadata_eligible(raw):
                break
            content = _rect(raw.get('content_bbox'))
            physical = fitz.Rect(box)
            if content is None or not physical.contains(content):
                break
            roi = content*(72/dpi)
            if not page.rect.contains(roi):
                break
            glyphs = _collect(paths, roi)
            if glyphs is None:
                break
            shape = tuple(sorted((_contours(path), bool(path.get('even_odd'))) for path in glyphs))
            if fingerprint is not None and shape != fingerprint:
                break
            try:
                if displaylist is None:
                    displaylist = page.get_displaylist(annots=False)
                ink, render = _render_paths(page, glyphs, displaylist=displaylist)
            except ValueError:
                break
            value = dict(raw, ink=ink)
            if notices._prepare(value, box, number) is None:
                break
            rendered.append(value)
            fingerprint = shape
            details[(number, box)] = dict(path_seqnos=[p['seqno'] for p in glyphs],
                topology=[list(entry) for entry in shape], content_bbox=list(content),
                render=render)
        else:
            # Keep duplicate records together, allowing the existing matcher
            # to veto conflicting raw labels, codes or OCR rather than lose one.
            all_rendered.extend(rendered)
            by_key[(number, box)] = rendered[0]
    evidence = []
    # Do not split raw seeds before matching: even a different vector topology
    # must not hide a conflicting label that the original matcher would veto.
    for match in notices.match_repeated_notices(all_rendered):
        supports = [notices._prepare(by_key[(match['page'], box)], box, match['page'])
                    for box in match['support_bboxes']]
        detail = details[(match['page'], match['bbox'])]
        if any(details[(match['page'], box)]['topology'] != detail['topology']
               for box in match['support_bboxes']):
            continue
        # Both frozen raw seeds must also agree with each other. Candidate
        # similarity is not transitive and cannot establish that agreement.
        support_score = notices._coverage(*supports)
        if not notices._similar_geometry(*supports) or support_score < notices._MIN_SCORE:
            continue
        record = dict(match)
        record['vector_evidence'] = dict(method_version=_METHOD, **detail,
            content_bbox_units='source_pixels', source_dpi=dpi, support_pair_score=support_score,
            supports=[dict(bbox=list(box), **details[(match['page'], box)])
                      for box in match['support_bboxes']])
        evidence.append(record)
    return sorted(evidence, key=lambda item: (item['page'], item['bbox']))
