"""Recover an unreadable date from its weekday and two aligned calendar anchors."""
from collections import Counter
from datetime import timedelta
from statistics import median
import re


def normalize_weekday_label(text):
    match=re.fullmatch(r'[\W_]*(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[\W_]*',
                       str(text),re.I)
    return match.group(1).capitalize() if match else None


def unresolved_date_rows(items, rows, *, left, right, top, bottom, row_gap, boundaries):
    """A visible date row may not disappear merely because its label failed OCR."""
    from core import schedule_core as sc
    result={}
    for item in items:
        x,y=sc._item_center(item)
        if not (x<left and top<y<bottom):
            continue
        if not (normalize_weekday_label(item['text']) or sc._is_english_date_token(item['text'])):
            continue
        if any(abs(y-row['y'])<row_gap*.45 for row in rows):
            continue
        y0=max((v for v in boundaries if v<y),default=top)
        y1=min((v for v in boundaries if v>y),default=bottom)
        if y1-y0>row_gap*1.8:
            continue
        result[(y0,y1)]=dict(kind='unresolved_date_row',bbox=[0,y0,float(right),y1])
    return list(result.values())


def recover_weekday_dates(rows, items):
    from core import schedule_core as sc
    ordered=sorted({r['date']:r for r in rows}.values(),key=lambda r:r['y'])
    gaps=[b['y']-a['y'] for a,b in zip(ordered,ordered[1:])
          if (b['date']-a['date']).days==1 and a['date'].weekday()<4 and b['y']>a['y']]
    if not gaps:
        return rows
    spacing=median(gaps)
    weekdays=[i for i in items if i['text'] in sc.ENGLISH_WEEKDAY_MAP]
    result=list(rows)
    for item in weekdays:
        y=sc._item_center(item)[1]
        if any(abs(y-r['y'])<max(3,spacing*.18) for r in ordered):
            continue
        # A readable contradictory date is an input conflict, not missing OCR.
        if any(sc._is_english_date_token(i['text'])
               and abs(i['x0']-item['x0'])<=max(20,item['x1']-item['x0'])
               and 0<=i['y0']-item['y0']<=2.8*max(10,item['y1']-item['y0']) for i in items):
            continue
        target=sc.WEEKDAYS.index(sc.ENGLISH_WEEKDAY_MAP[item['text']])
        votes=[]
        for anchor in ordered:
            delta=target-anchor['date'].weekday()
            if not delta or abs(y-(anchor['y']+delta*spacing))>max(4,spacing*.2):
                continue
            aligned=any(abs(sc._item_center(i)[1]-anchor['y'])<max(3,spacing*.18)
                        and abs(i['x0']-item['x0'])<=max(20,item['x1']-item['x0']) for i in weekdays)
            if aligned:
                votes.append(anchor['date']+timedelta(days=delta))
        counts=Counter(votes)
        if len(counts)==1 and next(iter(counts.values()))>=2:
            day=next(iter(counts))
            result.append(dict(weekday_text=item['text'],date=day,y=y,
                               date_evidence='weekday_and_two_same_week_anchors'))
    return result


def recover_date_only_edges(rows, items):
    """Join a printed date at a page cut to two consecutive same-week anchors."""
    from core import schedule_core as sc
    ordered=sorted(rows,key=lambda r:r['y'])
    if len(ordered)<2:
        return rows
    result=list(rows)
    for item in items:
        if not sc._is_english_date_token(item['text']):
            continue
        day=sc._parse_date(item['text']);y=sc._item_center(item)[1]
        if any(day==r['date'] for r in rows):
            continue
        if y<ordered[0]['y']:
            anchors=ordered[:2];edge=anchors[0];delta=-1
        elif y>ordered[-1]['y']:
            anchors=ordered[-2:];edge=anchors[-1];delta=1
        else:
            continue
        if any(a.get('date_evidence') for a in anchors):
            continue
        if ((anchors[1]['date']-anchors[0]['date']).days!=1
                or day!=edge['date']+timedelta(days=delta)
                or day-timedelta(days=day.weekday()) != edge['date']-timedelta(days=edge['date'].weekday())):
            continue
        gap=anchors[1]['y']-anchors[0]['y']
        if not gap*.25 < abs(y-edge['y']) < gap*1.3:
            continue
        aligned=all(any(sc._is_english_date_token(i['text'])
                    and sc._parse_date(i['text'])==anchor['date']
                    and abs(i['x0']-item['x0'])<=max(15,(item['x1']-item['x0'])*.4)
                    for i in items) for anchor in anchors)
        if aligned:
            result.append(dict(date=day,weekday_text=day.strftime('%A'),y=y,
                               date_evidence='printed_date_and_two_neighbor_anchors'))
    return result
