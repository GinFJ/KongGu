"""Read timetable cell geometry from visible borders, never equal-width guesses."""
from __future__ import annotations

from collections import Counter
from typing import Any

import cv2
import numpy as np


def _positions(values: Any, gap: int = 2) -> list[int]:
    groups: list[list[int]] = []
    for value in map(int, values):
        if groups and value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [(group[0] + group[-1]) // 2 for group in groups]


def uniform_row_height(bounds: list[int]) -> float | None:
    """Return untrimmed body-row height only for a near-uniform grid."""
    gaps = np.diff(np.asarray(bounds, dtype=float))
    if len(gaps) < 3 or float(gaps.min()) <= 0:
        return None
    height = float(gaps.mean())
    if float(gaps.max()-gaps.min()) > max(2.0, height*.08):
        return None
    return height


def english_header_items(gray: Any) -> list[dict[str, Any]]:
    """Locate numbered slots in the dated web grid, including blank spacers."""
    height, width = gray.shape[:2]
    ink = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    row_support = (ink > 0).sum(axis=1)
    ys = _positions(np.where(row_support > row_support.max() * .7)[0])
    typical = float(np.median(np.diff(ys))) if len(ys) > 2 else 0
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, round(typical * .55)))))
    headers = []
    # date, 1, 2, spacer, 3, 4, noon, 5, 6, spacer, 7, 8, spacer, 9, 10, 11
    columns = [1, 2, 4, 5, 7, 8, 10, 11, 13, 14, 15]
    for top, bottom in zip(ys, ys[1:]):
        if not 8 <= bottom - top <= typical * .8:
            continue
        xs = _positions(np.where((vertical[top+2:bottom-2] > 0).mean(axis=0) > .7)[0], 6)
        if len(xs) != 17:
            continue
        widths = [xs[i+1]-xs[i] for i in columns]
        if min(widths) <= 0 or max(widths) > min(widths) * 1.6:
            continue
        for period, column in enumerate(columns, 1):
            center = (xs[column] + xs[column+1]) / 2
            headers.append({'x0':center-1,'x1':center+1,'y0':(top+bottom)/2-2,
                'y1':(top+bottom)/2+2,'text':str(period),'border_header':True,
                'header_top':top,'header_bottom':bottom,
                'cell_left':xs[column],'cell_right':xs[column+1]})
    return headers


def vertical_grid_lines(gray: Any, row_gap: float) -> Any:
    """Keep continuous dividers while removing short letter strokes.

    A pale antialiased line remains a line; its length is relative to the
    observed row spacing instead of a fixed count of dark pixels.
    """
    # Extract locally dark, thin structures. An absolute grey threshold also
    # retains an entire shaded course cell and mistakes that fill for a line.
    pixels = np.clip(gray, 0, 255).astype('uint8')
    window = max(3, round(row_gap * .1)) | 1
    # The local bright envelope preserves both edges of a thick divider too;
    # a closing would lose any dark stripe as wide as its horizontal window.
    background = cv2.dilate(pixels,
        cv2.getStructuringElement(cv2.MORPH_RECT, (window, 1)))
    contrast = cv2.subtract(background, pixels)
    ink = (contrast > 25).astype('uint8') * 255
    length = max(8, round(row_gap * .6))
    return cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, length)))


def english_course_row_items(gray: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exclude partial time headers using the visible bounds of dated rows."""
    from core import schedule_core as sc

    days = sc._complete_english_day_rows(sc._find_english_day_rows(items))
    if len(days) < 3:
        return items
    ink = (gray < 200).astype('uint8') * 255
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, gray.shape[1]//25), 1)))
    support = (horizontal > 0).sum(axis=1)
    ys = _positions(np.where(support > support.max()*.7)[0])
    if len(ys) < 4:
        return items
    bands = []
    for day in days:
        # Continuation pages may start/end inside a row. Preserve their visible
        # portion rather than disabling the other rows' geometry checks.
        top = max((y for y in ys if y < day['y']), default=0)
        bottom = min((y for y in ys if y > day['y']), default=gray.shape[0])
        bands.append((top, bottom))
    return [item for item in items
            if any(top < sc._item_center(item)[1] < bottom for top, bottom in bands)
            or str(item.get('text', '')).strip().strip('.。') in {str(p) for p in range(1, 12)}]


def parse_chinese_bordered_image(
    gray: Any, name: str, items: list[dict[str, Any]], *, page: int = 0,
    evidence_out: list[dict[str, Any]] | None = None,
    vector_page: Any | None = None, vector_dpi: int = 180,
) -> list[dict[str, Any]]:
    from core import schedule_core as sc

    if gray is None or not items:
        return []
    height, width = gray.shape[:2]
    ink = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, width // 80), 1)))
    row_support = (horizontal > 0).sum(axis=1)
    ys = _positions(np.where(row_support > row_support.max() * .6)[0], 3)
    if len(ys) < 12:
        return []
    # Validate a visible six-slot header per day. The week column adds one edge.
    header_index = None
    edges: list[int] = []
    for index, (top, bottom) in enumerate(zip(ys[:6], ys[1:7])):
        if bottom - top < 6:
            continue
        band = ink[top + 2:bottom - 2]
        xs = _positions(np.where((band > 0).mean(axis=0) > .8)[0])
        if 43 <= len(xs) <= 44:
            header_index, edges = index, xs
            break
    if header_index is None:
        return []
    body = ys[header_index + 1:]
    typical = float(np.median(np.diff(body[:7])))
    stop = next((i + 1 for i, gap in enumerate(np.diff(body)) if gap > typical * 1.8), len(body))
    body = body[:stop]
    if not 8 <= len(body) - 1 <= 30:
        return []
    scale_height = uniform_row_height(body)
    sx, sy = sc._ocr_to_visual_scale(items, width, height)
    # Locate the week column from the table, not a fixed page x coordinate.
    week_items = [
        {**item, 'x0': 0, 'x1': 50}
        for item in items
        if edges[0] < sc._item_center(item)[0] * sx < edges[1]
    ]
    anchors = sc._find_chinese_full_term_week_rows(week_items)
    offsets = []
    for anchor in anchors:
        y = anchor['y'] * sy
        for index, (top, bottom) in enumerate(zip(body, body[1:])):
            if top < y < bottom:
                offsets.append(anchor['week'] - index - 1)
                break
    if not offsets:
        return []
    offset, support = Counter(offsets).most_common(1)[0]
    if support < 2 or support < len(offsets) * .6:
        return []
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(8, round(typical * .7)))))
    text_mask = cv2.subtract(ink, cv2.bitwise_or(horizontal, vertical))
    entries = sc._prepare_visual_entries(items, sx, sy)
    raw_entries = [(item, sc._item_center(item)[0]*sx, sc._item_center(item)[1]*sy)
                   for item in items]
    labels = ['1-2', '3-4', '午', '5-6', '7-8', '9-11']
    rows = []
    regions = []
    # Collect physical cells before matching: support must come from separate
    # regions on this page, never duplicate OCR boxes or an inferred label.
    for index, (top, bottom) in enumerate(zip(body, body[1:])):
        week = index + 1 + offset
        row_entries = [e for e in entries if top + 2 < e[3] < bottom - 2]
        raw_row = [(item, x) for item, x, y in raw_entries if top+2 < y < bottom-2]
        # Letter stems inside a merged notice are not cell boundaries. Use
        # continuous table dividers, not raw text pixels, to bound the notice.
        row_edges = _positions(np.where((vertical[top+3:bottom-3] > 0).mean(axis=0) > .8)[0])
        rows.append((week, top, bottom, row_entries, row_edges))
        bounded_edges = [x for x in row_edges if edges[1]-2 <= x <= edges[-1]+2]
        pad = max(1, round((scale_height or bottom-top)*.1))
        vector_pad = max(1.0, (scale_height or bottom-top)*.1)
        for left, right in zip(bounded_edges, bounded_edges[1:]):
            raw_items = [item for item, x in raw_row if left < x < right]
            tolerance = max(1, round((scale_height or bottom-top)*.05))
            # A pixel crop cannot prove a crossing OCR word is complete.
            # Vector collection separately checks every actual glyph path;
            # an imprecise OCR rectangle must not discard that source evidence.
            pixel_eligible = not any(
                float(item['x0'])*sx < left-tolerance or float(item['x1'])*sx > right+tolerance
                or float(item['y0'])*sy < top-tolerance or float(item['y1'])*sy > bottom+tolerance
                for item in raw_items)
            texts = [str(item.get('text', '')) for item in raw_items]
            logical = [(column, label) for column, label in enumerate(labels*7)
                       if column+2 < len(edges)
                       and left < (edges[column+1]+edges[column+2])/2 < right
                       and label != '午']
            weekdays = {sc.WEEKDAYS[column//6] for column, _ in logical}
            if len(weekdays) != 1 or right-left <= 2*pad or bottom-top <= 2*pad:
                continue
            known = next((text.strip() for text in texts if sc._is_empty_or_non_class(text)), None)
            regions.append(dict(bbox=(left, top, right, bottom), page=page,
                pixel_eligible=pixel_eligible,
                content_bbox=(left+vector_pad, top+vector_pad, right-vector_pad, bottom-vector_pad),
                ink=text_mask[top+pad:bottom-pad, left+pad:right-pad], texts=texts,
                has_course_code=any(sc._looks_like_plausible_chinese_encoded_course_cell(text)
                                    for text in texts), known_label=known,
                original_ocr=[dict(text=str(item.get('text', '')),
                    bbox=[float(item.get(axis, 0))*scale for axis, scale in
                          [('x0',sx),('y0',sy),('x1',sx),('y1',sy)]]) for item in raw_items],
                week=week, weekday=next(iter(weekdays)),
                periods=sorted({period for _, label in logical for period in sc.PERIOD_GROUPS[label]})))
    from core.visual_notice_consensus import match_repeated_notices
    vector_matches = []
    if vector_page is not None:
        from core.vector_notice_consensus import match_vector_notices
        vector_matches = match_vector_notices(vector_page, regions, dpi=vector_dpi)
    vector_boxes = {tuple(match['bbox']) for match in vector_matches}
    inferred = vector_matches + [match for match in match_repeated_notices(
                                     [region for region in regions if region['pixel_eligible']])
                                 if tuple(match['bbox']) not in vector_boxes]
    by_box = {tuple(region['bbox']): region for region in regions}
    for match in inferred:
        region = by_box[tuple(match['bbox'])]
        vector_match = tuple(match['bbox']) in vector_boxes
        if evidence_out is not None:
            evidence_out.append(dict(kind='same_page_nonclass_consensus', page=page,
                page_width=width, page_height=height, bbox=list(match['bbox']),
                original_ocr=region['original_ocr'], canonical_label=match['label'],
                week=region['week'], weekday=region['weekday'], periods=region['periods'],
                seeds=[dict(page=page, bbox=list(box),
                            original_ocr=by_box[tuple(box)]['original_ocr'], label=match['label'],
                            coverage_score=score)
                       for box, score in zip(match['support_bboxes'], match['scores'])],
                match=dict(coverage_score=min(match['scores']), threshold=.98,
                           method_version='same-page-vector-glyph-v1' if vector_match else 'same-page-glyph-v1'),
                vector_evidence=match.get('vector_evidence') if vector_match else None,
                action='excluded_from_preview_occupancy' if vector_match else 'suggested_nonclass_requires_review',
                occupancy_changed=vector_match,
                requires_review=True))
    blocks = []
    # A missing rightmost border is not extrapolated. Only bounded cells count.
    for week, top, bottom, row_entries, row_edges in rows:
        empty_regions = []
        empty_regions.extend((match['bbox'][0], match['bbox'][2]) for match in vector_matches
                             if match['bbox'][1] == top and match['bbox'][3] == bottom)
        for entry in row_entries:
            if not sc._is_empty_or_non_class(entry[4]):
                continue
            left = max((x for x in row_edges if x < entry[2]), default=0)
            right = min((x for x in row_edges if x > entry[2]), default=width)
            empty_regions.append((left, right))
        for column, (left, right) in enumerate(zip(edges[1:], edges[2:])):
            label = labels[column % 6]
            if label == '午':
                continue
            if any(a < (left+right)/2 < b for a,b in empty_regions):
                continue
            cell_entries = [e for e in row_entries if left < e[2] < right]
            texts = [e[4] for e in cell_entries]
            if any('校外' in text for text in texts):
                continue
            if not sc._visual_cell_has_occupancy(text_mask, left, top + 1, right, bottom - 1,
                                               cell_entries, scale_height=scale_height):
                continue
            course = '视觉表格占用'
            weekday = sc.WEEKDAYS[column // 6]
            for period in sc.PERIOD_GROUPS.get(label, []):
                block = sc._block(name, '中方', week, sc._date_for_weekday(week, weekday),
                    weekday, period, course, page=page, bbox=(left, top, right, bottom),
                    page_width=width, page_height=height)
                block['text_source'] = '视觉表格'
                block['bordered_grid'] = True
                block['recognized_text'] = texts
                blocks.append(block)
    return blocks
