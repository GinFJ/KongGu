from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import schedule_core


ZH_KIND = "中方"
EN_KIND = "英方"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit timetable PDF archive parsing quality.")
    parser.add_argument("root", help="Root directory containing department timetable PDFs.")
    parser.add_argument("--out-dir", default="outputs/training_audit", help="Directory for audit CSV files.")
    parser.add_argument("--parse", action="store_true", help="Run parser audit after inventory.")
    parser.add_argument("--offset", type=int, default=0, help="Start index for parser audit.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum files to parse; 0 means all.")
    parser.add_argument("--skip-image", action="store_true", help="Skip low-text/image-like PDFs in parser audit.")
    parser.add_argument("--only-image", action="store_true", help="Only parse low-text/image-like PDFs.")
    args = parser.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    configure_audit_cache(out_dir)

    rows = build_inventory(root)
    inventory_path = out_dir / "pdf_inventory.csv"
    write_csv(inventory_path, rows)
    print(json.dumps(inventory_summary(rows), ensure_ascii=False))
    print(inventory_path.resolve())

    if args.parse:
        suffix = audit_suffix(args.offset, args.limit, skip_image=args.skip_image, only_image=args.only_image)
        parse_path = out_dir / f"parse_audit{suffix}.csv"
        parse_rows = parse_inventory(
            rows,
            offset=args.offset,
            limit=args.limit,
            skip_image=args.skip_image,
            only_image=args.only_image,
            output_path=parse_path,
        )
        print(json.dumps(parse_summary(parse_rows), ensure_ascii=False))
        print(parse_path.resolve())
        if args.only_image and not args.offset and not args.limit:
            report_path = out_dir / "image_samples_report.md"
            write_image_report(report_path, parse_rows)
            print(report_path.resolve())

    return 0


def audit_suffix(offset: int, limit: int, *, skip_image: bool, only_image: bool) -> str:
    if offset or limit:
        return f"_{offset}_{limit or 'all'}"
    if only_image:
        return "_image_samples"
    if skip_image:
        return "_text_samples"
    return ""


def build_inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*.pdf")):
        rel = path.relative_to(root)
        parts = rel.parts
        department = parts[0] if len(parts) > 0 else ""
        role = parts[1] if len(parts) > 1 else ""
        folder_kind = parts[2].replace("课表", "") if len(parts) > 2 else ""
        inferred_kind = schedule_core.infer_pdf_kind(path.name, str(path)) or folder_kind
        name = schedule_core._extract_name_from_filename(path.name)
        pages, text_len, word_count = inspect_pdf_text(path)
        suspicious = []
        if "路人甲" in path.name:
            suspicious.append("filename_dummy")
        if inferred_kind not in {ZH_KIND, EN_KIND}:
            suspicious.append("unknown_kind")
        if not name:
            suspicious.append("name_not_extracted")
        if text_len < 50:
            suspicious.append("image_or_low_text")
        if path.stat().st_size > 450_000 and inferred_kind == EN_KIND:
            suspicious.append("large_english_maybe_image")
        rows.append(
            {
                "path": str(path),
                "relative_path": str(rel),
                "department": department,
                "role": role,
                "folder_kind": folder_kind,
                "inferred_kind": inferred_kind,
                "name": name,
                "pages": pages,
                "text_len": text_len,
                "word_count": word_count,
                "size": path.stat().st_size,
                "content_hash": file_sha256(path),
                "duplicate_group": "",
                "duplicate_names": "",
                "duplicate_kinds": "",
                "suspicious": ";".join(suspicious),
            }
        )
    annotate_duplicate_content(rows)
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def annotate_duplicate_content(rows: list[dict[str, object]]) -> None:
    by_hash: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        content_hash = str(row.get("content_hash") or "")
        if content_hash:
            by_hash.setdefault(content_hash, []).append(row)

    group_index = 1
    for content_hash, group in sorted(by_hash.items()):
        if len(group) < 2:
            continue
        names = sorted({str(row.get("name") or "") for row in group if str(row.get("name") or "")})
        kinds = sorted({str(row.get("inferred_kind") or "") for row in group if str(row.get("inferred_kind") or "")})
        duplicate_group = f"dup-{group_index:03d}"
        group_index += 1
        for row in group:
            row["duplicate_group"] = duplicate_group
            row["duplicate_names"] = ",".join(names)
            row["duplicate_kinds"] = ",".join(kinds)
            flags = set(filter(None, str(row.get("suspicious") or "").split(";")))
            flags.add("duplicate_content")
            if len(names) > 1:
                flags.add("duplicate_name_mismatch")
            if len(kinds) > 1:
                flags.add("duplicate_kind_mismatch")
            row["suspicious"] = ";".join(sorted(flags))


def inspect_pdf_text(path: Path) -> tuple[int, int, int]:
    try:
        doc = fitz.open(str(path))
        pages = len(doc)
        text_parts = []
        word_count = 0
        for page in doc:
            text_parts.append(page.get_text("text"))
            word_count += len(page.get_text("words"))
        doc.close()
        return pages, len("\n".join(text_parts).strip()), word_count
    except Exception:
        return 0, 0, 0


def parse_inventory(
    rows: list[dict[str, object]],
    *,
    offset: int,
    limit: int,
    skip_image: bool,
    only_image: bool,
    output_path: Path | None = None,
) -> list[dict[str, object]]:
    if only_image:
        rows = [
            row
            for row in rows
            if "image_or_low_text" in str(row.get("suspicious", ""))
            and "filename_dummy" not in str(row.get("suspicious", ""))
        ]
    selected = rows[offset:]
    if limit:
        selected = selected[:limit]

    parsed_rows: list[dict[str, object]] = []
    writer_handle = None
    writer = None
    fieldnames = [
        "index",
        "relative_path",
        "kind",
        "name",
        "blocks",
        "week_min",
        "week_max",
        "weeks",
        "methods",
        "errors",
        "status",
        "suspicious",
        "layout_profile",
        "ocr_items",
        "ocr_sample",
    ]
    if output_path is not None:
        writer_handle = output_path.open("w", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(writer_handle, fieldnames=fieldnames)
        writer.writeheader()
    for index, row in enumerate(selected, start=offset):
        flags = set(filter(None, str(row.get("suspicious", "")).split(";")))
        preflight_error = preflight_parse_error(flags)
        if preflight_error:
            parsed_row = parse_preflight_row(index, row, preflight_error)
            parsed_rows.append(parsed_row)
            if writer is not None and writer_handle is not None:
                writer.writerow(parsed_row)
                writer_handle.flush()
            print(index, Path(str(row["path"])).name, 0, "preflight")
            continue
        if skip_image and "image_or_low_text" in flags:
            parsed_row = parse_skip_row(index, row, "skipped_by_flag")
            parsed_rows.append(parsed_row)
            if writer is not None and writer_handle is not None:
                writer.writerow(parsed_row)
                writer_handle.flush()
            continue

        path = Path(str(row["path"]))
        source = {
            "name": path.name,
            "path": str(path),
            "kind": str(row.get("inferred_kind") or ""),
            "content": b"",
        }
        try:
            blocks, _calendar, errors, _preview = schedule_core.parse_actual_pdf_sources([source])
        except Exception as exc:
            blocks = []
            errors = [str(exc)]
        weeks = sorted({int(block["week"]) for block in blocks if block.get("week") is not None})
        methods = Counter(str(block.get("text_source") or "direct") for block in blocks)
        parsed_row = {
            "index": index,
            "relative_path": row["relative_path"],
            "kind": row["inferred_kind"],
            "name": row["name"],
            "blocks": len(blocks),
            "week_min": min(weeks) if weeks else "",
            "week_max": max(weeks) if weeks else "",
            "weeks": ",".join(map(str, weeks)),
            "methods": json.dumps(dict(methods), ensure_ascii=False),
            "errors": " | ".join(errors),
            "suspicious": row.get("suspicious", ""),
        }
        parsed_row["status"] = classify_parse_status(parsed_row)
        parsed_row["layout_profile"] = layout_profile(source, parsed_row["kind"])
        if only_image:
            parsed_row.update(ocr_evidence(source))
        parsed_rows.append(parsed_row)
        if writer is not None and writer_handle is not None:
            writer.writerow(parsed_row)
            writer_handle.flush()
        print(index, path.name, len(blocks), "errors" if errors else "ok")
    if writer_handle is not None:
        writer_handle.close()
    return parsed_rows


def parse_skip_row(index: int, row: dict[str, object], reason: str) -> dict[str, object]:
    return {
        "index": index,
        "relative_path": row["relative_path"],
        "kind": row["inferred_kind"],
        "name": row["name"],
        "blocks": "",
        "week_min": "",
        "week_max": "",
        "weeks": "",
        "methods": "",
        "errors": reason,
        "status": "skipped",
        "suspicious": row.get("suspicious", ""),
        "layout_profile": "",
        "ocr_items": "",
        "ocr_sample": "",
    }


def parse_preflight_row(index: int, row: dict[str, object], error: str) -> dict[str, object]:
    parsed_row = {
        "index": index,
        "relative_path": row["relative_path"],
        "kind": row["inferred_kind"],
        "name": row["name"],
        "blocks": 0,
        "week_min": "",
        "week_max": "",
        "weeks": "",
        "methods": "",
        "errors": error,
        "suspicious": row.get("suspicious", ""),
        "layout_profile": "",
        "ocr_items": "",
        "ocr_sample": "",
    }
    parsed_row["status"] = classify_parse_status(parsed_row)
    return parsed_row


def preflight_parse_error(flags: set[str]) -> str:
    if "filename_dummy" in flags:
        return "文件名包含测试或占位姓名“路人甲”，不应进入正式空课统计。"
    if "duplicate_name_mismatch" in flags:
        return "同一 PDF 内容出现在多个成员姓名下，疑似复制错课表，需人工确认真实归属。"
    if "duplicate_kind_mismatch" in flags:
        return "同一 PDF 内容同时出现在多个课表类型下，需人工确认中方/英方归属。"
    return ""


def configure_audit_cache(out_dir: Path) -> None:
    cache_root = out_dir / "cache"
    defaults = {
        "KONGGU_PARSE_CACHE": cache_root / "parsed_blocks",
        "KONGGU_OCR_TEXT_CACHE": cache_root / "pdf_text",
        "KONGGU_OCR_LAYOUT_CACHE": cache_root / "pdf_layout",
        "MPLCONFIGDIR": cache_root / "mplconfig",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))


def ocr_evidence(source: dict[str, object]) -> dict[str, object]:
    try:
        items = schedule_core._extract_pdf_ocr_items(source)  # noqa: SLF001 - audit tool needs parser evidence.
    except Exception as exc:
        return {"ocr_items": "", "ocr_sample": f"OCR evidence failed: {exc}"}
    tokens = [
        str(item.get("text") or "").strip()
        for item in sorted(items, key=lambda value: (int(value.get("page", 0)), float(value.get("y0", 0)), float(value.get("x0", 0))))
        if str(item.get("text") or "").strip()
    ]
    sample = " / ".join(tokens[:24])
    if len(sample) > 280:
        sample = sample[:277] + "..."
    return {"ocr_items": len(items), "ocr_sample": sample}


def layout_profile(source: dict[str, object], kind: object) -> str:
    try:
        return schedule_core.detect_schedule_layout_profile(source, str(kind))
    except Exception as exc:
        return f"profile_error:{exc}"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def inventory_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "total": len(rows),
        "by_kind": dict(Counter(str(row["inferred_kind"]) for row in rows)),
        "by_department": dict(Counter(str(row["department"]) for row in rows)),
        "image_or_low_text": sum("image_or_low_text" in str(row["suspicious"]) for row in rows),
        "duplicate_content": sum("duplicate_content" in str(row["suspicious"]) for row in rows),
        "duplicate_name_mismatch": sum("duplicate_name_mismatch" in str(row["suspicious"]) for row in rows),
        "suspicious": sum(bool(row["suspicious"]) for row in rows),
    }


def parse_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    parsed_rows = [row for row in rows if str(row.get("status") or "") != "skipped"]
    return {
        "total": len(rows),
        "parsed": len(parsed_rows),
        "skipped": statuses.get("skipped", 0),
        "with_errors": sum(bool(row["errors"]) for row in parsed_rows),
        "no_blocks": sum(int(row["blocks"] or 0) == 0 for row in parsed_rows),
        "blocks_total": sum(int(row["blocks"] or 0) for row in parsed_rows),
        "by_status": dict(statuses),
    }


def classify_parse_status(row: dict[str, object]) -> str:
    flags = set(filter(None, str(row.get("suspicious") or "").split(";")))
    errors = str(row.get("errors") or "")
    kind = str(row.get("kind") or "")
    blocks = int(row.get("blocks") or 0)
    if "filename_dummy" in flags:
        return "needs_review_placeholder_filename"
    if "duplicate_name_mismatch" in flags:
        return "needs_review_duplicate_name_mismatch"
    if "duplicate_kind_mismatch" in flags:
        return "needs_review_duplicate_kind_mismatch"
    if not errors and blocks:
        return "auto_accepted"
    if kind == ZH_KIND and "低置信" in errors:
        return "needs_review_low_chinese_confidence"
    if kind == ZH_KIND and "姓名" in errors and "不一致" in errors:
        return "needs_review_name_mismatch"
    if kind == EN_KIND and "覆盖不足" in errors:
        return "needs_review_low_english_coverage"
    if errors:
        return "needs_review_error"
    return "needs_review_empty"


def write_image_report(path: Path, rows: list[dict[str, object]]) -> None:
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    lines = [
        "# 图片型课表样本审计",
        "",
        f"共 {len(rows)} 个图片型/低文本 PDF 样本。",
        "",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| 序号 | 类型 | 姓名 | 占用数 | 状态 | 版式 profile | 周次 | 文件 | 原因 |",
            "|---:|---|---|---:|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {index} | {kind} | {name} | {blocks} | {status} | {layout_profile} | {weeks} | {relative_path} | {errors} |".format(
                index=row.get("index", ""),
                kind=row.get("kind", ""),
                name=row.get("name", ""),
                blocks=row.get("blocks", ""),
                status=row.get("status", ""),
                layout_profile=row.get("layout_profile", ""),
                weeks=row.get("weeks", ""),
                relative_path=row.get("relative_path", ""),
                errors=brief_error(row.get("errors", "")),
            )
        )
    lines.extend(
        [
            "",
            "## 使用口径",
            "",
            "- `auto_accepted`：当前解析器可自动产出占用记录，可进入空课计算，但仍建议抽样核对图片型源文件。",
            "- `needs_review_low_chinese_confidence`：中方图片型课表 OCR 碎片过多，自动识别结果不可信，已阻止进入空课计算。",
            "- `needs_review_name_mismatch`：课表正文姓名与文件名姓名不一致，疑似传错或复制错课表，已阻止进入空课计算。",
            "- `needs_review_placeholder_filename`：文件名含 `路人甲` 等测试占位姓名，已阻止进入空课计算。",
            "- `needs_review_duplicate_name_mismatch`：同一 PDF 内容出现在多个成员姓名下，需人工确认真实归属。",
            "- `needs_review_duplicate_kind_mismatch`：同一 PDF 内容出现在多个课表类型下，需人工确认中方/英方归属。",
            "- `needs_review_low_english_coverage`：英方图片型课表 OCR 只识别到极少占用，覆盖不足，已阻止进入空课计算。",
            "- `needs_review_error`：其它解析错误，需要人工确认。",
            "",
            "## OCR 摘要",
            "",
            "| 序号 | OCR token 数 | OCR 样本 |",
            "|---:|---:|---|",
        ]
    )
    for row in rows:
        if str(row.get("status") or "") == "auto_accepted":
            continue
        lines.append(
            "| {index} | {ocr_items} | {ocr_sample} |".format(
                index=row.get("index", ""),
                ocr_items=row.get("ocr_items", ""),
                ocr_sample=str(row.get("ocr_sample", "")).replace("|", "/"),
            )
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def brief_error(error: object, limit: int = 120) -> str:
    text = str(error or "").replace("|", "/").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
