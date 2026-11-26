"""Observed date coverage for dated English grids, independent of busy cells."""
from __future__ import annotations

import re
from typing import Any


def _query_window(items, core):
    rows=core._find_english_day_rows(items)
    if not rows:
        return None
    first_row=min(r['y'] for r in rows)
    labels=[i for i in items if i['y0']<first_row and re.search(r'\b(?:Start|End)\s*:',str(i['text']),re.I)]
    text=' '.join(str(i['text']) for i in labels)
    if not (re.search(r'\bStart\s*:',text,re.I) and re.search(r'\bEnd\s*:',text,re.I)):
        return None
    candidates=[]
    for item in items:
        if not min(i['y0'] for i in labels)<=item['y0']<first_row:
            continue
        for match in re.finditer(r'(?<!\d)20\d{2}[-/]?\d{2}[-/]?\d{2}(?!\d)',str(item['text'])):
            try:
                candidates.append((item['x0']+match.start(),core._parse_date(match.group()).isoformat()))
            except ValueError:
                pass
    dates=[value for _,value in sorted(candidates)]
    if len(dates)!=2 or dates[0]>dates[1]:
        return {'unreadable':True}
    return dict(start=dates[0],end=dates[1])


def _printed_column_dates(items, core):
    """A page break can separate a readable date from its weekday label."""
    weekdays=[i for i in items if i['text'] in core.ENGLISH_WEEKDAY_MAP]
    if not weekdays:
        return set()
    from statistics import median
    left=median(i['x0'] for i in weekdays)
    tolerance=max(15,median(i['x1']-i['x0'] for i in weekdays))
    has_query=any(re.search(r'\bStart\s*:',str(i['text']),re.I) for i in items)
    first=min(i['y0'] for i in weekdays) if has_query else -1
    dates=set()
    for item in items:
        if abs(item['x0']-left)>tolerance or item['y0']<first:
            continue
        text=str(item['text']).strip()
        if re.fullmatch(r'20\d{2}[-/]?\d{2}[-/]?\d{2}',text):
            try:
                dates.add(core._parse_date(text).isoformat())
            except ValueError:
                pass
    return dates


def english_grid_coverage(source: dict[str, Any], core: Any, *, allow_ocr: bool) -> dict[str, Any] | None:
    document = core._open_pdf_document(source)
    if document is None:
        return None
    pages = []
    try:
        for page in document:
            pages.append([dict(x0=w[0],y0=w[1],x1=w[2],y1=w[3],text=core._normalize_ocr_text(w[4]))
                          for w in page.get_text('words')])
    finally:
        document.close()
    # Only use OCR for a page already diagnosed as requiring it. All full-page
    # extraction here shares the source-bound OCR cache with the actual parser.
    if allow_ocr:
        missing = [index for index, words in enumerate(pages) if not core._find_english_day_rows(words)]
        if missing:
            items = core._extract_pdf_ocr_items(source)
            for index in missing:
                pages[index] = [i for i in items if int(i.get('page', 0)) == index]
    dates = sorted({row['date'].isoformat() for words in pages
                    for row in core._complete_english_day_rows(core._find_english_day_rows(words))}
                   | set().union(*(_printed_column_dates(words,core) for words in pages)))
    if not any(re.search(r'Timetable\s*for', ' '.join(str(i['text']) for i in words), re.I)
               or core._find_english_period_header_rows(words) for words in pages):
        # Other timetable formats have their own coverage contracts.
        return None
    windows=[window for words in pages if (window:=_query_window(words,core)) is not None]
    for window in windows:
        # The website can print a full week of empty rows beyond its End filter.
        # Those rows are not evidence that no courses were scheduled.
        dates=[] if window.get('unreadable') else [d for d in dates if window['start']<=d<=window['end']]
    return dict(source_hash=core._source_digest(source), dates=dates,
                query_windows=windows, method='observed_dated_rows_with_query_bounds')


def covered_weeks(inspections: list[dict[str, Any]], calendar: Any, weeks: list[int]) -> list[int]:
    coverages = [i['date_coverage'] for i in inspections if i.get('date_coverage') is not None]
    if not coverages:
        return list(weeks)
    for item in inspections:
        coverage = item.get('date_coverage')
        if coverage is not None and coverage.get('source_hash') != item.get('source_hash'):
            raise ValueError('课表日期范围与来源不一致，请重新解析。')
    date_sets = [set(c['dates']) for c in coverages]
    dates_by_week = {}
    for row in calendar.to_dict("records"):
        if row["weekday"] in {"周一", "周二", "周三", "周四", "周五"}:
            dates_by_week.setdefault(int(row["week"]), set()).add(str(row["date"])[:10])
    common_dates = set.intersection(*date_sets)
    return [week for week in weeks if len(dates_by_week.get(week, ())) == 5
            and dates_by_week[week] <= common_dates]
