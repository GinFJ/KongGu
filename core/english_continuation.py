"""Match a cut-off weekday with a printed continuation date, without guessing."""
from datetime import timedelta


def link_page_edge_dates(pages, sizes, core):
    """Return copied word pages and unresolved visible edge-row evidence.

    The printed date must match the weekday, column, and two neighboring
    calendar anchors. Page order supplies adjacency, never a date itself.
    """
    from core.english_dates import normalize_weekday_label
    output = [[dict(item) for item in items] for items in pages]
    notes = []
    rows = [core._find_english_day_rows(items) for items in pages]
    for index, items in enumerate(pages):
        width, height = sizes[index]
        headers = core._find_english_period_header_rows(items)
        if not width or not height or not headers:
            continue
        header = headers[-1]
        column_left = min(c['center'] for c in header['columns'])
        for item_index, item in enumerate(items):
            weekday = normalize_weekday_label(item['text'])
            x, y = core._item_center(item)
            if not weekday or x >= column_left or y < height * .9 or y <= header['y']:
                continue
            if any(abs(y - row['y']) < max(4, item['y1']-item['y0']) for row in rows[index]):
                continue
            candidates = []
            contradictory_date = any(core._is_english_date_token(i['text'])
                and abs(i['x0']-item['x0']) < 20
                and 0 <= i['y0']-item['y0'] <= 2.8*max(10,item['y1']-item['y0'])
                for i in items)
            if not contradictory_date and index + 1 < len(pages):
                next_items = pages[index+1]
                next_width, next_height = sizes[index+1]
                if abs(width-next_width) <= max(2,width*.01):
                    anchors = [r for r in rows[index+1] if not r.get('date_evidence')]
                    for date_item in next_items:
                        if not core._is_english_date_token(date_item['text']):
                            continue
                        if core._item_center(date_item)[1] > min(50,next_height*.1):
                            continue
                        if abs(date_item['x0']-item['x0']) > max(15,(date_item['x1']-date_item['x0'])*.4):
                            continue
                        day = core._parse_date(date_item['text'])
                        if core.WEEKDAYS[day.weekday()] != core.ENGLISH_WEEKDAY_MAP[weekday]:
                            continue
                        following = [r for r in anchors if r['date'] > day]
                        next_proof = (len(following)>=2
                            and following[0]['date']==day+timedelta(days=1)
                            and following[1]['date']==day+timedelta(days=2))
                        previous = [r for r in rows[index] if not r.get('date_evidence')]
                        prior_proof = (len(previous)>=2
                            and (previous[-1]['date']-previous[-2]['date']).days==1
                            and 0 < (day-previous[-1]['date']).days <= 3)
                        if next_proof or prior_proof:
                            candidates.append(day)
            if len(set(candidates)) == 1:
                day = candidates[0]
                output[index][item_index].update(
                    text=f'{weekday} {day.isoformat()}',
                    date_evidence='weekday_and_next_page_printed_date',
                    date_source_page=index+1)
            else:
                notes.append(dict(kind='unresolved_date_row',page=index,
                    bbox=[0,max(header['y'],item['y0']-20),width,height]))
    return output, notes
