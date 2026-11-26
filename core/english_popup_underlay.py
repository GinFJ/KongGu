"""Prove blank cells below an embedded course-details popup.

We never erase a popup and treat the resulting hole as empty. The PDF must
retain all four original vector cell borders and no content inside them.
An underlying image, glyph, unknown paint, or missing border fails closed.
"""
from __future__ import annotations

import re


def _stroke_boxes(drawing, scale):
    """Bound straight strokes separately; a row frame does not fill its interior."""
    segments=[]
    for item in drawing.get('items',[]):
        kind=item[0]
        if kind=='l':
            segments.append((item[1],item[2]))
        elif kind in {'re','qu'}:
            shape=item[1]
            points=[shape.tl,shape.tr,shape.br,shape.bl] if kind=='re' else [shape.ul,shape.ur,shape.lr,shape.ll]
            segments.extend(zip(points,points[1:]+points[:1]))
        else:
            return None  # curves / unknown subpaths cannot prove emptiness
    if not segments:
        return None
    if drawing.get('closePath'):
        segments.append((segments[-1][1],segments[0][0]))
    half=float(drawing.get('width') or 0)*scale/2
    return [[min(a.x,b.x)*scale-half,min(a.y,b.y)*scale-half,
             max(a.x,b.x)*scale+half,max(a.y,b.y)*scale+half] for a,b in segments]


def popup_bbox(items):
    labels = [i for i in items if re.match(
        r'^(Start|Length|Module|Group|Room|Teacher)\s*[:：]',str(i['text']),re.I)]
    keys = {re.split(r'\s*[:：]',str(i['text']),maxsplit=1)[0].lower() for i in labels}
    if not {'start','length','module'}.issubset(keys):
        return None
    return [min(i['x0'] for i in labels),min(i['y0'] for i in labels),
            max(i['x1'] for i in labels),max(i['y1'] for i in labels)]


def inspect_underlay(page, items, dpi=120):
    bbox=popup_bbox(items)
    if bbox is None or not hasattr(page,'get_bboxlog'):
        return None
    scale=dpi/72
    try:
        paints=page.get_bboxlog()
        drawings={d['seqno']:d for d in page.get_drawings()}
    except Exception:
        return None
    candidates=[]
    for seq,(kind,rect) in enumerate(paints):
        r=[float(v)*scale for v in rect]
        if (kind=='fill-image' and r[0]-2<=bbox[0] and r[1]-2<=bbox[1]
                and r[2]+2>=bbox[2] and r[3]+2>=bbox[3]
                and r[2]-r[0] <= (bbox[2]-bbox[0])*1.7
                and r[3]-r[1] <= (bbox[3]-bbox[1])*1.7
                and (r[2]-r[0])*(r[3]-r[1]) < float(page.rect.width*page.rect.height)*scale*scale*.25):
            candidates.append(((r[2]-r[0])*(r[3]-r[1]),seq,r))
    if not candidates:
        return None
    _,popup_seq,image_bbox=min(candidates)
    content=[];borders=[]
    for seq,(kind,rect) in enumerate(paints):
        if seq==popup_seq:
            continue
        drawing=drawings.get(seq,{})
        fill=drawing.get('fill')
        if kind=='fill-path' and fill and min(fill)>=.98:
            continue  # plain white paper / table backgrounds
        boxes=_stroke_boxes(drawing,scale) if kind=='stroke-path' else None
        simple_fill=kind=='fill-path' and bool(drawing.get('items')) and all(
            item[0] in {'re','qu'} for item in drawing['items'])
        eligible=seq<popup_seq and (boxes is not None or simple_fill)
        boxes=boxes or [[float(v)*scale for v in rect]]
        content.extend(dict(bbox=r,border_eligible=eligible) for r in boxes)
        if eligible:
            borders.extend(boxes)
    return dict(content=content,borders=borders,image_bbox=image_bbox)


def cell_is_proven_blank(evidence, bbox):
    if not evidence:
        return False
    x0,y0,x1,y1=bbox
    tolerance=max(3.5,min(x1-x0,y1-y0)*.08)
    inner=(x0+1,y0+1,x1-1,y1-1)
    if inner[0]>=inner[2] or inner[1]>=inner[3]:
        return False
    def intersects(a,b):
        return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
    def is_border(r):
        # Adjacent row/column strokes can overlap a rounded raster boundary.
        # Ink classification only needs alignment with an observed edge;
        # the separate four-border proof below still requires full coverage.
        vertical=(r[2]-r[0]<=tolerance*2
                  and any(abs((r[0]+r[2])/2-x)<=tolerance for x in (x0,x1)))
        horizontal=(r[3]-r[1]<=tolerance*2
                    and any(abs((r[1]+r[3])/2-y)<=tolerance for y in (y0,y1)))
        return vertical or horizontal
    if any(intersects(entry['bbox'],inner) and not (
            entry['border_eligible'] and is_border(entry['bbox'])) for entry in evidence['content']):
        return False
    vertical=lambda x: any(abs((r[0]+r[2])/2-x)<=tolerance
        and r[2]-r[0]<=tolerance*2 and r[1]<=y0+tolerance and r[3]>=y1-tolerance
        for r in evidence['borders'])
    horizontal=lambda y: any(abs((r[1]+r[3])/2-y)<=tolerance
        and r[3]-r[1]<=tolerance*2 and r[0]<=x0+tolerance and r[2]>=x1-tolerance
        for r in evidence['borders'])
    return vertical(x0) and vertical(x1) and horizontal(y0) and horizontal(y1)
