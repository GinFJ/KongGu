"""Re-read ambiguous calendar notices from small native PDF crops, locally."""
from copy import deepcopy
import contextlib
import io
import re

import cv2
import fitz
import numpy as np

from core.bordered_timetables import _positions


class _PageBorders:
    """Reuse page geometry across candidates; retain only one vertical mask."""
    def __init__(self, gray):
        self.height, self.width = gray.shape
        self.ink = (gray < 200).astype('uint8') * 255
        self.horizontal = cv2.morphologyEx(self.ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, self.width // 25), 1)))
        self.rows = _positions(np.where((self.horizontal > 0).sum(axis=1) > self.width * .35)[0])
        self.row_edges = {}
        self.kernel_height = 0
        self.vertical = None

    def edges(self, top, bottom):
        key = (top, bottom)
        if key not in self.row_edges:
            height = max(8, round((bottom - top) * .7))
            if height != self.kernel_height:
                self.vertical = cv2.morphologyEx(self.ink, cv2.MORPH_OPEN,
                    cv2.getStructuringElement(cv2.MORPH_RECT, (1, height)))
                self.kernel_height = height
            strip = self.vertical[top + 3:bottom - 3]
            self.row_edges[key] = _positions(np.where((strip > 0).mean(axis=0) > .85)[0])
        return self.row_edges[key]


def _wide_cell(borders, item, items, *, repeated_short=False, allow_course=False):
    """Require observed borders spanning several logical timetable columns."""
    height, width = borders.height, borders.width
    horizontal, ys = borders.horizontal, borders.rows
    x=(item['x0']+item['x1'])/2
    y=(item['y0']+item['y1'])/2
    above=[v for v in ys if v<y]; below=[v for v in ys if v>y]
    if not above or not below:return None
    top,bottom=max(above),min(below)
    if not 8 < bottom-top < height*.12:return None
    edges = borders.edges(top, bottom)
    lefts=[v for v in edges if v<x]; rights=[v for v in edges if v>x]
    # Some printed tables end in two horizontal strokes with no closing
    # vertical stroke. Use their observed common endpoint, never page width.
    if not rights:
        ends=[]
        for row in [top,bottom]:
            active=np.where(horizontal[max(0,row-1):row+2].max(axis=0)>0)[0]
            if len(active):ends.append(int(active[-1]))
        if len(ends)==2 and abs(ends[0]-ends[1])<=3 and min(ends)>item['x1']+3:
            rights=[min(ends)]
    if not lefts or not rights:return None
    left,right=max(lefts),min(rights)
    if right-left<width*(.025 if repeated_short else .15):return None
    for other in items:
        ox=(other['x0']+other['x1'])/2;oy=(other['y0']+other['y1'])/2
        if (not allow_course and left<ox<right and top<oy<bottom and re.search(r'[A-Za-z0-9]',str(other['text']))
                and not (repeated_short and other is item)):
            return None
    return [left,top,right,bottom]


def _near_notice(text,label):
    if len(text)==len(label):
        return sum(a!=b for a,b in zip(text,label))==1
    return len(text)+1==len(label) and any(label[:i]+label[i+1:]==text for i in range(len(label)))


def refine_notice_items(source, items, core):
    """Keep raw OCR unchanged unless two native-resolution reads agree.

    This is a parser-stage refinement, so raw OCR caches stay reusable. Its
    evidence is tied to the source, parser and OCR signatures in parse caches.
    """
    key=(core._source_digest(source),core.cache_signature(),core._ocr_cache_signature())
    memo=source.get('_notice_refinement_cache')
    if memo and memo['key']==key:
        return deepcopy(memo['items'])
    labels={s for s in core.NON_CLASS_KEYWORDS if re.fullmatch(r'[\u4e00-\u9fff]{2,6}',s)}
    candidates=[]
    for index,item in enumerate(items):
        text=str(item.get('text','')).strip()
        if (not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]{1,6}',text)
                or core._is_empty_or_non_class(text)):
            continue
        short_labels=set()
        if len(text)<=2 and float(item.get('confidence') or 0)<.65:
            x,y=core._item_center(item)
            for label in labels:
                if len(label)!=2:continue
                peers=[other for other in items if other.get('page',0)==item.get('page',0)
                       and other['text']==label and float(other.get('confidence') or 0)>=.9
                       and abs(core._item_center(other)[0]-x)<max(8,(item['x1']-item['x0'])*.5)]
                if (len(peers)>=3 and any(core._item_center(p)[1]<y for p in peers)
                        and any(core._item_center(p)[1]>y for p in peers)):
                    short_labels.add(label)
        if len(short_labels)==1:
            candidates.append((index,item,short_labels))
        elif len(text)>=2 and any(_near_notice(text,label) for label in labels):
            candidates.append((index,item,set()))
    if not candidates:return items
    result=deepcopy(items)
    document=core._open_pdf_document(source)
    if document is None:return items
    try:
        last_page = None
        for index,item,short_labels in candidates[:16]:
            core.raise_if_cancelled()
            page_number=int(item.get('page',0))
            page=document[page_number]
            if page_number != last_page:
                image=core._page_image(source,page,page_number,120)
                if image is None:continue
                gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
                borders = _PageBorders(gray)
                page_items=[i for i in items if int(i.get('page',0))==page_number]
                last_page = page_number
            bounds=_wide_cell(borders,item,page_items,repeated_short=bool(short_labels))
            if bounds is None:continue
            observations=[]
            # Coordinates above are the raw 120-dpi OCR frame. Render each
            # crop directly from the PDF instead of magnifying those pixels.
            clip=fitz.Rect((bounds[0]+3)*.6,(bounds[1]+3)*.6,
                           (bounds[2]-3)*.6,(bounds[3]-3)*.6) & page.rect
            try:
                engine=core._build_ocr_engine()
                for dpi in [240,360]:
                    pix=page.get_pixmap(dpi=dpi,clip=clip,alpha=False)
                    crop=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3]
                    crop=cv2.copyMakeBorder(crop,20,20,20,20,cv2.BORDER_CONSTANT,value=(255,255,255))
                    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                        prediction=engine.ocr(crop) if hasattr(engine,'ocr') else engine.predict(crop)
                    recognized=core._extract_ocr_items_from_result(prediction,page_number,crop.shape[1],crop.shape[0])
                    words=[r for r in recognized if str(r['text']).strip()]
                    if len(words)!=1 or words[0]['text'] not in (short_labels or labels) or float(words[0].get('confidence') or 0)<.90:
                        break
                    observations.append(dict(dpi=dpi,text=words[0]['text'],confidence=words[0]['confidence']))
            except Exception:
                # Optional refinement cannot erase the original low-confidence
                # occupancy. The ordinary review gate still applies.
                continue
            if len(observations)!=2 or observations[0]['text']!=observations[1]['text']:
                continue
            label=observations[0]['text']
            result[index].update(text=label,original_text=item['text'],notice_refinement=observations,
                                 verified_notice_bounds=bounds)
            note=dict(kind='native_crop_notice',page=page_number,bbox=bounds,
                page_width=image.shape[1],page_height=image.shape[0],dpi=120,
                canonical_label=label,original_ocr=[deepcopy(item)],observations=observations,
                source_hash=key[0],parser_signature=key[1],ocr_signature=key[2],
                action='excluded_from_preview_occupancy',requires_review=False)
            source.setdefault('_visual_notice_evidence',[]).append(note)
    finally:
        document.close()
    source['_notice_refinement_cache']=dict(key=key,items=deepcopy(result))
    return result
