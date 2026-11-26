"""Cell-first parsing of dated English grids, including clipped continuation rows.

Text recognition supplies labels; visible cell boundaries establish occupancy.
A nonempty cell with incomplete text is retained and explicitly sent to review.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from core.bordered_timetables import _positions, vertical_grid_lines
from core.english_clock_axis import fit_clock_axis, midday_periods
from core.english_popup_underlay import popup_bbox, cell_is_proven_blank


def _content_pixels(gray: Any) -> bool:
    if gray.size == 0:
        return False
    # Subtract the local paper/background shade. A grey empty cell is not ink.
    paper = float(np.percentile(gray, 85))
    ink = (gray < paper - 35).astype('uint8')
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components = [s for s in stats[1:count] if s[4] >= 3 and s[2] >= 2 and s[3] >= 2
                  and s[2] < gray.shape[1] * .9 and s[3] < gray.shape[0] * .9]
    return len(components) >= 2 and sum(int(s[4]) for s in components) >= 12


def parse_dated_cells(gray: Any, items: list[dict[str, Any]], name: str,
                      *, page: int, evidence_out: list[dict[str, Any]] | None = None,
                      popup_underlay: dict[str, Any] | None = None,
                      row_candidate_items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]] | None:
    """Return None only when this page has no supported, observed grid template."""
    from core import schedule_core as sc

    headers = sc._find_english_period_header_rows(items)
    days = sc._complete_english_day_rows(sc._find_english_day_rows(items))
    if not days or not headers or not all(
        all('cell_left' in c and 'cell_right' in c for c in h['columns']) for h in headers
    ):
        return None
    overlay_bbox = popup_bbox(items)
    overlay_note = None
    unresolved_overlay = False
    resolved_cells = []
    if overlay_bbox:
        overlay_note = dict(kind='overlay',page=page,bbox=overlay_bbox)
        if evidence_out is not None:
            evidence_out.append(overlay_note)
    height, width = gray.shape
    gaps = [b['y']-a['y'] for a,b in zip(days,days[1:]) if (b['date']-a['date']).days == 1]
    row_gap = float(np.median(gaps)) if gaps else 50.0
    # PDF rescaling can make a one-pixel border pale. Find its local contrast,
    # just as for vertical borders, rather than dropping rows at a fixed grey.
    background = cv2.dilate(gray, cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(3, round(row_gap * .1)) | 1)))
    ink = (cv2.subtract(background, gray) > 25).astype('uint8') * 255
    slot_width = min(c['cell_right']-c['cell_left'] for h in headers for c in h['columns'])
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,(max(10, round(slot_width*.5)),1)))
    support = (horizontal > 0).sum(axis=1)
    ys = _positions(np.where(support > float(support.max())*.7)[0])
    if len(ys) < 3:
        return None
    vertical = vertical_grid_lines(gray, row_gap)
    if evidence_out is not None:
        from core.english_dates import unresolved_date_rows
        left=min(c['cell_left'] for h in headers for c in h['columns'])
        right=max(c['cell_right'] for h in headers for c in h['columns'])
        active=np.where((vertical[:,max(0,round(left)-2):min(width,round(right)+3)]>0).sum(axis=1)>=6)[0]
        if len(active):
            evidence_out.extend({**note,'page':page} for note in unresolved_date_rows(
                row_candidate_items if row_candidate_items is not None else items,days,
                left=left,right=right,top=int(active.min()),bottom=int(active.max())+1,
                row_gap=row_gap,boundaries=ys))
    blocks = []
    for day in days:
        y = float(day['y'])
        header = max((h for h in headers if h['y'] < y),key=lambda h:h['y'],default=headers[0])
        columns = header['columns']
        left = round(min(c['cell_left'] for c in columns))
        right = round(max(c['cell_right'] for c in columns))
        # Visible vertical borders locate the cut edge of a continuation. Do
        # not extend its first row into the browser title above the table.
        active = np.where((vertical[:,max(0,left-2):min(width,right+3)] > 0).sum(axis=1) >= 6)[0]
        above = [v for v in ys if v < y]
        below = [v for v in ys if v > y]
        if not above or not below:
            # A page can retain less than the normal divider opening length.
            # Locate that cut using short strokes at known column boundaries,
            # never by extending the row into the browser title/footer.
            edge_lines = vertical_grid_lines(gray, row_gap * .3)
            edges = sorted({round(c[key]) for c in columns
                            for key in ('cell_left', 'cell_right')})
            aligned = np.zeros(height, dtype=np.int32)
            for edge in edges:
                strip = edge_lines[:, max(0, edge-2):min(width, edge+3)]
                if strip.size:
                    aligned += (strip.max(axis=1) > 0).astype(np.int32)
            observed = np.where(aligned >= 6)[0]
            if len(observed):
                active = observed
        top = max(above) if above else int(active.min()) if len(active) else 0
        bottom = min(below) if below else int(active.max())+1 if len(active) else height
        if not top < y < bottom or bottom-top > row_gap*1.8:
            raise ValueError('英方课表日期行边界不完整，无法可靠确定课程格；请检查原页或重新导出 PDF。')
        row_vertical = (vertical_grid_lines(gray, bottom-top)
                        if bottom-top < row_gap*.6 else vertical)
        # Unnumbered lunch cells use the confirmed clock profile only when
        # every numbered column and the observed course boundaries agree.
        before_noon = next(c for c in columns if int(c['period']) == 4)
        after_noon = next(c for c in columns if int(c['period']) == 5)
        noon_left, noon_right = before_noon['cell_right'], after_noon['cell_left']
        noon_words = [i for i in items if noon_left < sc._item_center(i)[0] < noon_right
                      and top < sc._item_center(i)[1] < bottom]
        axis = fit_clock_axis(columns)
        strip = row_vertical[max(0,top+3):min(height,bottom-3),:]
        noon_edges = [float(noon_left)] + [float(x) for x in _positions(
            np.where((strip > 0).mean(axis=0) > .6)[0])
            if noon_left+3 < x < noon_right-3] + [float(noon_right)]
        midday_cells = []
        unresolved_noon = False
        for start,end in zip(noon_edges,noon_edges[1:]):
            covered = bool(overlay_bbox and start < overlay_bbox[2] and end > overlay_bbox[0]
                           and top < overlay_bbox[3] and bottom > overlay_bbox[1])
            if covered and cell_is_proven_blank(popup_underlay,(start,top,end,bottom)):
                resolved_cells.append(dict(date=day['date'].isoformat(),periods=[],
                                           bbox=[start,top,end,bottom]))
                continue
            words = [i for i in noon_words if start < sc._item_center(i)[0] < end]
            crop = gray[max(0,top+3):min(height,bottom-3),round(start)+3:round(end)-3]
            if not any(not sc._is_english_grid_noise_token(str(i['text'])) for i in words) and not _content_pixels(crop):
                continue
            periods = midday_periods(axis,start,end)
            if periods:
                midday_cells.append([dict(period=p,cell_left=start,cell_right=end,
                                          center=(start+end)/2,clock_axis=True) for p in periods])
            else:
                unresolved_noon = True
                if covered:
                    unresolved_overlay = True
        if unresolved_noon and evidence_out is not None:
            evidence_out.append(dict(kind='unnumbered_midday',page=page,date=day['date'].isoformat(),
                bbox=[float(noon_left),float(top),float(noon_right),float(bottom)]))
        # A missing divider joins adjacent *physical* period cells only. Blank
        # lunchtime/break columns must never be crossed.
        cells: list[list[dict[str, Any]]] = []
        for column in columns:
            if cells:
                previous = cells[-1][-1]
                shared = abs(previous['cell_right']-column['cell_left']) <= 2
                boundary = round((previous['cell_right']+column['cell_left'])/2)
                strip = row_vertical[max(0,top+3):min(height,bottom-3),max(0,boundary-2):min(width,boundary+3)]
                divider = float((strip.max(axis=1)>0).mean()) if strip.size else 1.0
                if shared and divider < .35:
                    cells[-1].append(column)
                    continue
            cells.append([column])
        cells.extend(midday_cells)
        for cell in cells:
            x0, x1 = float(cell[0]['cell_left']), float(cell[-1]['cell_right'])
            obstructed = bool(overlay_bbox and x0 < overlay_bbox[2] and x1 > overlay_bbox[0]
                              and top < overlay_bbox[3] and bottom > overlay_bbox[1])
            if obstructed:
                if all(cell_is_proven_blank(popup_underlay,
                        (float(c['cell_left']),top,float(c['cell_right']),bottom)) for c in cell):
                    resolved_cells.append(dict(date=day['date'].isoformat(),
                        periods=[int(c['period']) for c in cell],bbox=[x0,top,x1,bottom]))
                    continue
                unresolved_overlay = True
            words = [i for i in items if x0 < sc._item_center(i)[0] < x1 and top < sc._item_center(i)[1] < bottom
                     and not i.get('border_header')]
            words.sort(key=lambda i:(i['y0'],i['x0']))
            tokens = [str(i['text']).strip() for i in words]
            course = '' if obstructed else sc._merge_english_grid_course_tokens(tokens)
            if course and sc._is_empty_or_non_class(course):
                continue
            crop = gray[max(0,top+3):min(height,bottom-3),max(0,round(x0)+3):min(width,round(x1)-3)]
            if not course and not obstructed and not _content_pixels(crop):
                continue
            # Group/room text is evidence that the cell was not empty; it must
            # not be silently discarded when the short course code was missed.
            partial = not course
            label = course or '待核对占用（课程文字不完整）'
            for column in cell:
                block = sc._block(name,'英方',sc._week_from_date(day['date']),day['date'],
                    sc.WEEKDAYS[day['date'].weekday()],int(column['period']),label,
                    page=page,bbox=(x0,float(top),x1,float(bottom)),page_width=width,page_height=height)
                block['provenance'] = dict(method='dated_visible_cell',
                    cell_text_incomplete=partial, observed_tokens=tokens,
                    obstructed=obstructed,
                    date_evidence=day.get('date_evidence','printed_date_and_weekday'),
                    periods=[int(c['period']) for c in cell])
                if column.get('clock_axis'):
                    block['provenance']['time_mapping'] = 'validated_clock_axis'
                blocks.append(block)
    if overlay_note and resolved_cells and not unresolved_overlay:
        overlay_note.update(kind='overlay_verified_empty',
                            method='original_pdf_empty_bordered_cells',cells=resolved_cells)
    return blocks
