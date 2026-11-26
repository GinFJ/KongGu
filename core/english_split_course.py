"""Read page-split course labels from adjacent visible fragments, locally."""
from collections import defaultdict
from datetime import date
import contextlib
import io
import re

import cv2
import fitz
import numpy as np


def fragment_bounds(previous, current, box):
    """Locate an open bottom fragment with the same two physical cell edges."""
    x0, top, x1, bottom = map(int, box)
    if previous.shape[1] != current.shape[1] or top > current.shape[0]*.12:
        return None
    mask=cv2.morphologyEx((previous<180).astype('uint8'),cv2.MORPH_OPEN,
                         np.ones((5,1),np.uint8))
    if x0<3 or x1>=previous.shape[1]-3 or x1-x0<12:
        return None
    active=np.where((mask[:,x0-2:x0+3].max(axis=1)>0)
                    & (mask[:,x1-2:x1+3].max(axis=1)>0))[0]
    if not len(active) or active[-1]<previous.shape[0]*.9:
        return None
    end=int(active[-1])+1
    background=cv2.dilate(previous,np.ones((5,1),np.uint8))
    horizontal_ink=cv2.subtract(background,previous)>25
    horizontal=np.where(horizontal_ink[:,x0+3:x1-3].mean(axis=1)>.8)[0]
    before=[int(y) for y in horizontal if y<end-3]
    if not before:
        return None
    start=max(before)+2
    if not 5<=end-start<=current.shape[0]*.08:
        return None
    # A closed bottom cell belongs to the previous row, not this continuation.
    if (previous[end-3:end,x0+3:x1-3]<180).mean(axis=1).max()>.8:
        return None
    if np.mean(mask[start:end,x0-2:x0+3].max(axis=1)>0)<.85:
        return None
    if np.mean(mask[start:end,x1-2:x1+3].max(axis=1)>0)<.85:
        return None
    from core.english_bordered_grid import _content_pixels
    if not _content_pixels(previous[start:end,x0+3:x1-3]):
        return None
    return (x0+3,start,x1-3,end),(x0+3,top,x1-3,bottom-3)


def recover_split_courses(source, blocks, items, core):
    pending=defaultdict(list)
    for block in blocks:
        evidence=block.get('provenance') or {}
        if (evidence.get('cell_text_incomplete') and not evidence.get('obstructed')
                and block.get('page',0)>0 and block.get('bbox')):
            pending[(block['page'],tuple(block['bbox']),block['date'])].append(block)
    if not pending:
        return blocks
    pages=defaultdict(list)
    for item in items:pages[int(item.get('page',0))].append(item)
    document=core._open_pdf_document(source)
    if document is None:return blocks
    try:
        for (page,box,day_text),targets in pending.items():
            core.raise_if_cancelled()
            preceding=core._find_english_day_rows(pages[page-1])
            following=core._find_english_day_rows(pages[page])
            day=date.fromisoformat(day_text)
            if (not preceding or not following or following[0]['date']!=day
                    or not 1<=(day-preceding[-1]['date']).days<=3):
                continue
            # Only the first printed row on the next adjacent page is eligible.
            previous=core._page_image(source,document[page-1],page-1,120)
            current=core._page_image(source,document[page],page,120)
            if previous is None or current is None:continue
            bounds=fragment_bounds(previous[:,:,:3].mean(2).astype('uint8'),
                                   current[:,:,:3].mean(2).astype('uint8'),box)
            if bounds is None:continue
            if bounds[0][1] <= preceding[-1]['y']:
                continue
            anchors=[str(t) for t in targets[0]['provenance']['observed_tokens']
                     if re.fullmatch(r'L\d[A-Z]-\d+',str(t))
                     or core._looks_like_room(str(t))]
            if not anchors:continue
            groups={a for a in anchors if re.fullmatch(r'L\d[A-Z]-\d+',a)}
            # The page break may have discarded part of a glyph altogether.
            # Two OCR passes can confidently make the same E/F mistake. A
            # recovered code must also occur in an intact cell for this group;
            # neither a guessed spelling nor a raw cut-off token is a seed.
            known_labels={b['course'] for b in blocks
                if b.get('date')!=day_text and not (b.get('provenance') or {}).get('cell_text_incomplete',True)
                and groups.intersection((b.get('provenance') or {}).get('observed_tokens',[]))}
            observations=[]
            try:
                engine=core._build_ocr_engine()
                for dpi in [240,360]:
                    parts=[]
                    for page_number,rect in zip([page-1,page],bounds):
                        clip=fitz.Rect(*(value*.6 for value in rect)) & document[page_number].rect
                        pix=document[page_number].get_pixmap(dpi=dpi,clip=clip,alpha=False)
                        parts.append(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3])
                    if parts[0].shape[1]!=parts[1].shape[1]:break
                    crop=np.concatenate(parts,axis=0)
                    crop=cv2.copyMakeBorder(crop,20,20,20,20,cv2.BORDER_CONSTANT,value=(255,255,255))
                    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                        result=engine.ocr(crop) if hasattr(engine,'ocr') else engine.predict(crop)
                    words=core._extract_ocr_items_from_result(result,page,crop.shape[1],crop.shape[0])
                    tokens=[str(w['text']).strip() for w in words]
                    label=core._merge_english_grid_course_tokens(tokens)
                    if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,24}',label)
                            or label not in known_labels
                            or not all(anchor in tokens for anchor in anchors)
                            or any(float(w.get('confidence') or 0)<.90 for w in words)):
                        break
                    observations.append(dict(dpi=dpi,label=label,tokens=tokens,
                        confidence=[float(w.get('confidence') or 0) for w in words]))
            except Exception:
                # Failure to read the fragments never removes known occupancy.
                continue
            if len(observations)!=2 or observations[0]['label']!=observations[1]['label']:
                continue
            proof=dict(method='adjacent_printed_row_fragments',pages=[page-1,page],
                       bounds=bounds,coordinate_dpi=120,observations=observations)
            for block in targets:
                block['course']=observations[0]['label']
                block['provenance']={**block['provenance'], 'cell_text_incomplete':False,
                                     'split_course_evidence':proof}
    finally:
        document.close()
    return blocks
