"""Recover a missing off-campus room only from two reads of its actual cell."""
from copy import deepcopy
import contextlib
import io
import re

import cv2
import fitz
import numpy as np

from core.ocr_notice_refinement import _PageBorders, _wide_cell


def refine_missing_locations(source, items, core):
    # This printed timetable family encodes courses as, for example, 思1R84.
    # A repeated alias elsewhere is only a trigger to look; it is never proof
    # that this particular occurrence has the same room.
    if not any('校外' in str(i.get('text','')) for i in items):
        return items
    candidates=[i for i in items if re.fullmatch(r'[\u4e00-\u9fff]{1,3}\d?[RL]\d{1,3}',str(i.get('text','')))]
    if not candidates:return items
    key=(core._source_digest(source),core.cache_signature(),core._ocr_cache_signature())
    memo=source.get('_location_refinement_cache')
    if memo and memo['key']==key:return deepcopy(memo['items'])
    result=deepcopy(items)
    doc=core._open_pdf_document(source)
    if doc is None:return items
    try:
        last_page = None
        for item in candidates:
            page_number=int(item.get('page',0))
            page=doc[page_number]
            if last_page != page_number:
                page_items=[i for i in items if int(i.get('page',0))==page_number]
                pixels=core._page_image(source,page,page_number,120)
                gray=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY)
                borders = _PageBorders(gray)
                last_page = page_number
            bounds=_wide_cell(borders,item,page_items,repeated_short=True,allow_course=True)
            if bounds is None:continue
            inside=[i for i in page_items if bounds[0]<core._item_center(i)[0]<bounds[2]
                    and bounds[1]<core._item_center(i)[1]<bounds[3]]
            if len(inside)!=1:continue  # A visible room is already accounted for.
            clip=fitz.Rect(*((v+(3 if n<2 else -3))*.6 for n,v in enumerate(bounds))) & page.rect
            observations=[]
            try:
                engine=core._build_ocr_engine()
                for dpi in [240,360]:
                    core.raise_if_cancelled()
                    pix=page.get_pixmap(dpi=dpi,clip=clip,alpha=False)
                    crop=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3]
                    crop=cv2.copyMakeBorder(crop,20,20,20,20,cv2.BORDER_CONSTANT,value=(255,255,255))
                    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                        prediction=engine.ocr(crop) if hasattr(engine,'ocr') else engine.predict(crop)
                    words=core._extract_ocr_items_from_result(prediction,page_number,crop.shape[1],crop.shape[0])
                    if len(words)!=2 or any(float(w.get('confidence') or 0)<.9 for w in words):break
                    texts=[str(w['text']).strip() for w in words]
                    locations=[t for t in texts if re.fullmatch(r'校外[\w-]+',t)]
                    if item['text'] not in texts or len(locations)!=1:break
                    observations.append(dict(dpi=dpi,location=locations[0],tokens=texts,
                                             confidence=[w['confidence'] for w in words]))
            except Exception:
                continue
            if len(observations)!=2 or observations[0]['location']!=observations[1]['location']:continue
            location=observations[0]['location']
            result.append(dict(item,text=location,x0=bounds[0]+3,x1=bounds[2]-3,
                               y0=bounds[1]+3,y1=bounds[3]-3,
                               location_refinement=observations,verified_notice_bounds=bounds))
            source.setdefault('_visual_notice_evidence',[]).append(dict(
                kind='native_crop_offcampus',page=page_number,bbox=bounds,dpi=120,
                page_width=gray.shape[1],page_height=gray.shape[0],canonical_label=location,
                original_ocr=[deepcopy(item)],observations=observations,
                source_hash=key[0],parser_signature=key[1],ocr_signature=key[2],
                action='excluded_from_preview_occupancy',requires_review=False))
    finally:
        doc.close()
    source['_location_refinement_cache']=dict(key=key,items=deepcopy(result))
    return result
