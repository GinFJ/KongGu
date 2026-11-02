"""Offline parser-layer perturbation audit; never an OCR or accuracy benchmark.

Manifest: {"records": [{"id": "S01", "pdf": "...", "ocr_layout": "...",
"kind": "中方"}], "semester_start": "2026-08-31", "teaching_weeks": 20}.
Keep the manifest, PDFs, cached text, and output outside the repository.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def transformed(data, items, scale=1.0, padding=0.0, reverse=False):
    import fitz

    result_items = []
    with fitz.open(stream=data, filetype="pdf") as original, fitz.open() as result:
        order = list(range(len(original)))
        if reverse:
            order.reverse()
        for page_index, old_index in enumerate(order):
            rect = original[old_index].rect
            dx, dy = rect.width * padding, rect.height * padding
            page = result.new_page(width=rect.width*(scale+2*padding),
                                   height=rect.height*(scale+2*padding))
            page.show_pdf_page(fitz.Rect(dx, dy, dx+rect.width*scale,
                                        dy+rect.height*scale), original, old_index)
            for item in items:
                if int(item.get("page", 0)) != old_index:
                    continue
                value = copy.deepcopy(item)
                for axes, length, offset, dimension in [
                    (("x0", "x1"), rect.width, dx, "page_width"),
                    (("y0", "y1"), rect.height, dy, "page_height"),
                ]:
                    for axis in axes:
                        value[axis] = (item[axis]/item[dimension]*length*scale+offset)*120/72
                value.update(page=page_index, page_width=page.rect.width*120/72,
                             page_height=page.rect.height*120/72)
                result_items.append(value)
        return result.tobytes(), result_items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination == ROOT or ROOT in destination.parents:
        parser.error("Output must be outside the repository (contains private OCR text).")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cache = destination / "derived-layout"
    cache.mkdir(exist_ok=True)
    os.environ.update(KONGGU_OCR_LAYOUT_CACHE=str(cache),
                      KONGGU_SEMESTER_START_DATE=manifest["semester_start"],
                      KONGGU_TEACHING_WEEKS=str(manifest["teaching_weeks"]))
    import cv2
    import fitz
    from core import schedule_core as sc

    cv2.setNumThreads(1)
    source_paths = [ROOT/"core/schedule_core.py", ROOT/"core/bordered_timetables.py",
                    ROOT/"core/signature.py", ROOT/"core/visual_notice_consensus.py",
                    ROOT/"core/vector_notice_consensus.py"]
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source_paths}

    def occupied(data, items, kind, name):
        digest = hashlib.sha256(data).hexdigest()
        payload = dict(version=sc.OCR_LAYOUT_CACHE_VERSION,
                       ocr_signature=sc._ocr_cache_signature(), hash=digest, items=items)
        (cache/f"{digest}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        source = dict(bytes=data, kind=kind, file_name=f"{name}-{kind}课表.pdf")
        blocks = (sc._parse_chinese_full_term_visual_occupancy(source, source["file_name"])
                  if kind == "中方" else sc._parse_english_ocr_week_grid(source, source["file_name"]))
        return {(b["week"], b["weekday"], b["period"]) for b in blocks}

    rows = []
    for number, record in enumerate(manifest["records"], 1):
        data = Path(record["pdf"]).read_bytes()
        payload = json.loads(Path(record["ocr_layout"]).read_text(encoding="utf-8"))
        if payload.get("hash") != hashlib.sha256(data).hexdigest():
            raise ValueError(f"Source/cache hash mismatch: S{number:02}")
        items, kind = payload["items"], record["kind"]
        if kind not in {"中方", "英方"}:
            raise ValueError("Unsupported kind")
        baseline = occupied(data, items, kind, "测试成员")
        with fitz.open(stream=data, filetype="pdf") as document:
            multipage = len(document) > 1
        for variant in ["rename_shuffle", "scale_080", "scale_125", "margin_020", "reverse_pages"]:
            if variant == "reverse_pages" and not multipage:
                continue
            if variant == "rename_shuffle":
                changed, coordinates = data, copy.deepcopy(items)
                random.Random(57).shuffle(coordinates)
            else:
                changed, coordinates = transformed(data, items,
                    scale={"scale_080": .8, "scale_125": 1.25}.get(variant, 1),
                    padding=.2 if variant == "margin_020" else 0,
                    reverse=variant == "reverse_pages")
            actual = occupied(changed, coordinates, kind, "另一成员")
            rows.append(dict(source=f"S{number:02}", kind=kind, variant=variant,
                             baseline=len(baseline), actual=len(actual),
                             missing=len(baseline-actual), extra=len(actual-baseline),
                             passed=bool(baseline) and actual == baseline))
        print(f"S{number:02} complete", flush=True)
    passed = sum(row["passed"] for row in rows)
    current = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in source_paths}
    report = dict(layer="parser_with_transformed_cached_ocr", accuracy_claim=False,
                  independent_unseen_samples=False, passed=passed, total=len(rows),
                  status="passed" if passed == len(rows) and rows and frozen == current else "blocked",
                  source_changed_during_audit=frozen != current, rows=rows,
                  source_hashes=frozen)
    (destination/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['status']}: {passed}/{len(rows)}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
