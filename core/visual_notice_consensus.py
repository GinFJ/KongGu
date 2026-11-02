"""Conservative same-page evidence for two repeated, canonical notices.

No inference here is an approval: raw OCR is retained by the caller and every
returned match requires review. Small glyph differences can be meaningful;
distance coverage is combined with geometry and stable topology safeguards.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Mapping
import unicodedata

import cv2
import numpy as np


_LABELS = frozenset({'教研', '不排课'})
_SHAPE = (64, 32)
_TOLERANCE = 2.0
_MIN_SCORE = .98


@dataclass(frozen=True, slots=True)
class _Region:
    bbox: tuple[float, float, float, float]
    page: int
    texts: tuple[str, ...]
    known_label: str | None
    mask: Any
    glyph_ratio: float
    topology: tuple[int, int]


def _box(value: Any) -> tuple[float, float, float, float] | None:
    try:
        box = tuple(float(v) for v in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _disjoint(a: _Region, b: _Region) -> bool:
    return (a.bbox[2] <= b.bbox[0] or b.bbox[2] <= a.bbox[0]
            or a.bbox[3] <= b.bbox[1] or b.bbox[3] <= a.bbox[1])


def _topology(mask: Any) -> tuple[int, int]:
    # Ignore tiny isolated dots, but retain substantial connected components.
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    components = sum(int(area) >= 16 for area in stats[1:, cv2.CC_STAT_AREA])
    background = 1-mask
    count, labels, areas, _ = cv2.connectedComponentsWithStats(background, connectivity=4)
    holes = 0
    for number in range(1, count):
        x, y, w, h, area = map(int, areas[number])
        if x == 0 or y == 0 or x+w == mask.shape[1] or y+h == mask.shape[0]:
            continue
        if area < 16:
            continue
        hole = (labels == number).astype('uint8')
        # A gap/loop larger than the matching tolerance is semantic evidence.
        # Smaller holes are unstable under the source raster's antialiasing.
        if float(cv2.distanceTransform(hole, cv2.DIST_L2, 3).max()) > 2*_TOLERANCE:
            holes += 1
    return int(components), int(holes)


def _prepare(raw: Mapping[str, Any], bbox: tuple[float, float, float, float], page: int) -> _Region | None:
    course_flag = raw.get('has_course_code')
    if not isinstance(course_flag, (bool, np.bool_)) or course_flag:
        return None
    texts = raw.get('texts')
    if not isinstance(texts, (list, tuple)) or not all(isinstance(t, str) for t in texts):
        return None
    terms = tuple(t.strip() for t in texts if t.strip())
    # A flag alone cannot reliably describe all code formats (e.g. 5L33).
    if any(re.search(r'[A-Za-z0-9]', unicodedata.normalize('NFKC', term)) for term in terms):
        return None
    ink = np.asarray(raw.get('ink'))
    if ink.ndim != 2 or ink.dtype != np.uint8 or ink.size == 0:
        return None
    ink = (ink > 0).astype('uint8') * 255
    points = cv2.findNonZero(ink)
    if points is None or float((ink > 0).mean()) > .9:
        return None
    x, y, width, height = cv2.boundingRect(points)
    if width < 2 or height < 2:
        return None
    normalized = cv2.resize(ink[y:y+height, x:x+width], _SHAPE, interpolation=cv2.INTER_AREA)
    mask = (normalized > 255*.2).astype('uint8')
    if not mask.any() or float(mask.mean()) > .9:
        return None
    label = raw.get('known_label')
    if label is not None and not isinstance(label, str):
        return None
    label = (label.strip() or None) if isinstance(label, str) else None
    return _Region(bbox, page, terms, label, mask, width/height, _topology(mask))


def _seed_label(region: _Region) -> str | None:
    terms = set(region.texts)
    if len(terms) != 1:
        return None
    label = next(iter(terms))
    if label not in _LABELS or region.known_label not in (None, label):
        return None
    return label


def _similar_geometry(a: _Region, b: _Region) -> bool:
    aw, ah = a.bbox[2]-a.bbox[0], a.bbox[3]-a.bbox[1]
    bw, bh = b.bbox[2]-b.bbox[0], b.bbox[3]-b.bbox[1]
    return all(abs(ratio-1) <= .25 for ratio in (aw/bw, ah/bh, a.glyph_ratio/b.glyph_ratio))


def _coverage(a: _Region, b: _Region) -> float:
    if a.topology != b.topology:
        return 0.0
    da = cv2.distanceTransform(1-a.mask, cv2.DIST_L2, 3)
    db = cv2.distanceTransform(1-b.mask, cv2.DIST_L2, 3)
    return min(float((da[b.mask > 0] <= _TOLERANCE).mean()),
               float((db[a.mask > 0] <= _TOLERANCE).mean()))


def match_repeated_notices(regions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return review evidence without reading files or changing raw OCR.

    Each region contains ``bbox``, 2-D uint8 ``ink`` (nonzero means glyph),
    ``texts`` and ``has_course_code``. Optional ``page`` defaults to zero;
    optional ``known_label`` only vetoes conflicts, never creates a seed.
    The caller should provide actual physical merged-cell regions and retain
    the original texts together with this evidence for human review.
    """
    grouped: dict[tuple[int, tuple[float, float, float, float]], list[Mapping[str, Any]]] = {}
    for raw in regions:
        if not isinstance(raw, Mapping):
            continue
        bbox = _box(raw.get('bbox'))
        page = raw.get('page', 0)
        if bbox is None or not isinstance(page, (int, np.integer)) or page < 0:
            continue
        grouped.setdefault((int(page), bbox), []).append(raw)
    prepared = []
    for (page, bbox), duplicates in grouped.items():
        values = [_prepare(raw, bbox, page) for raw in duplicates]
        if any(value is None for value in values):
            continue
        first = values[0]
        if any(value.texts != first.texts or value.known_label != first.known_label
               or value.glyph_ratio != first.glyph_ratio
               or not np.array_equal(value.mask, first.mask) for value in values[1:]):
            continue
        prepared.append(first)

    # Freeze strict source OCR seeds before examining any unknown candidate.
    seeds = [(region, _seed_label(region)) for region in prepared if _seed_label(region)]
    seeds = [(region, label) for region, label in seeds
             if not any(other.page == region.page and other_label != label
                        and not _disjoint(region, other) for other, other_label in seeds)]
    evidence = []
    for candidate in prepared:
        if candidate.known_label or any(t in _LABELS for t in candidate.texts):
            continue
        # Only the two short notice classes are inferred. Multiple different
        # OCR strings or a longer title are conflicting content, not permission
        # to replace a course even when its code flag was missed upstream.
        terms = set(candidate.texts)
        if len(terms) > 1 or any(len(term) > 3 for term in terms):
            continue
        matches = []
        for label in sorted(_LABELS):
            ranked = []
            for seed, seed_label in seeds:
                if seed_label != label or seed.page != candidate.page:
                    continue
                if not _disjoint(candidate, seed) or not _similar_geometry(candidate, seed):
                    continue
                score = _coverage(candidate, seed)
                if score >= _MIN_SCORE:
                    ranked.append((score, seed))
            ranked.sort(key=lambda item: (-item[0], item[1].bbox))
            support = []
            for score, seed in ranked:
                if all(_disjoint(seed, previous[1]) for previous in support):
                    support.append((score, seed))
                if len(support) == 2:
                    break
            if len(support) == 2:
                matches.append((label, support))
        if len(matches) != 1:
            continue
        label, support = matches[0]
        evidence.append(dict(bbox=candidate.bbox, page=candidate.page, label=label,
                             support_bboxes=[seed.bbox for _, seed in support],
                             scores=[score for score, _ in support], requires_review=True))
    return evidence
