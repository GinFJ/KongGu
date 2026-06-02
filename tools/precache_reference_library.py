from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CONFIG = ROOT / "config" / "reference_library.json"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(errors="replace")

import sitecustomize  # noqa: E402,F401
from core import schedule_core  # noqa: E402


def _configure_cache() -> None:
    cache_root = ROOT / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    env_paths = {
        "PADDLE_PDX_CACHE_HOME": cache_root / "paddlex",
        "PADDLE_HOME": cache_root / "paddle",
        "PADDLEOCR_HOME": cache_root / "paddleocr",
        "KONGGU_OCR_TEXT_CACHE": cache_root / "pdf_text",
        "KONGGU_OCR_LAYOUT_CACHE": cache_root / "pdf_layout",
        "KONGGU_PARSE_CACHE": cache_root / "parsed_blocks",
        "MPLCONFIGDIR": cache_root / "matplotlib",
    }
    for key, path in env_paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(key, str(path))
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def _load_reference_root() -> Path:
    config = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    root = Path(config["root_path"])
    if not root.exists():
        raise FileNotFoundError(f"参考库目录不存在：{root}")
    return root


def _source_from_pdf(path: Path) -> dict:
    return {
        "name": path.name,
        "path": str(path),
        "kind": schedule_core.infer_pdf_kind(path.name, str(path)) or "中方",
        "content": b"",
    }


def _needs_ocr(source: dict) -> tuple[bool, str]:
    text = schedule_core._extract_pdf_text(source)
    kind = source["kind"]
    if not schedule_core._is_usable_extracted_text(text, kind):
        return True, "内嵌文本质量不足"
    parsed = schedule_core._parse_chinese_text(text, source["name"]) if kind == "中方" else schedule_core._parse_english_text(text, source["name"])
    if not parsed:
        return True, "内嵌文本无法解析"
    return False, f"内嵌文本可用，{len(parsed)} 个时间块"


def _populate_parse_cache(source: dict) -> int:
    blocks, _calendar_df, errors, _preview_df = schedule_core.parse_actual_pdf_sources([source])
    if errors:
        raise RuntimeError("；".join(errors))
    return len(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description="批量预识别参考库 PDF，生成 OCR 文本缓存。")
    parser.add_argument("--force", action="store_true", help="即使缓存已存在，也重新 OCR。")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个 PDF，0 表示不限制。")
    args = parser.parse_args()

    _configure_cache()
    root = _load_reference_root()
    pdfs = sorted(root.rglob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    started = time.time()
    stats = {"total": len(pdfs), "cached": 0, "skipped_text": 0, "ocr": 0, "parsed": 0, "failed": 0}
    print(f"参考库：{root}", flush=True)
    print(f"待检查 PDF：{len(pdfs)}", flush=True)

    for index, pdf in enumerate(pdfs, start=1):
        source = _source_from_pdf(pdf)
        cache_path = schedule_core._ocr_cache_path(source)
        label = f"[{index}/{len(pdfs)}] {source['kind']} {pdf.name}"
        try:
            parsed_cache = schedule_core._load_parse_cache(source, source["kind"])
            if parsed_cache is not None and not args.force:
                stats["cached"] += 1
                stats["parsed"] += 1
                print(f"{label} -> 已有解析缓存，跳过 OCR/解析（{len(parsed_cache)} 个时间块）", flush=True)
                continue

            if cache_path.exists() and not args.force:
                stats["cached"] += 1
                block_count = _populate_parse_cache(source)
                stats["parsed"] += 1
                print(f"{label} -> 已有 OCR 缓存，解析缓存 {block_count} 个时间块", flush=True)
                continue

            needs_ocr, reason = _needs_ocr(source)
            if not needs_ocr:
                stats["skipped_text"] += 1
                block_count = _populate_parse_cache(source)
                stats["parsed"] += 1
                print(f"{label} -> {reason}，无需 OCR，解析缓存 {block_count} 个时间块", flush=True)
                continue

            print(f"{label} -> {reason}，开始 OCR", flush=True)
            text = schedule_core._extract_pdf_ocr_text(source)
            if text.strip():
                stats["ocr"] += 1
                block_count = _populate_parse_cache(source)
                stats["parsed"] += 1
                print(f"{label} -> OCR 完成，{len(text)} 字符，解析缓存 {block_count} 个时间块", flush=True)
            else:
                stats["failed"] += 1
                print(f"{label} -> OCR 未识别到文字", flush=True)
        except Exception as exc:
            stats["failed"] += 1
            print(f"{label} -> 失败：{exc}", flush=True)

    elapsed = time.time() - started
    print("\n预识别完成", flush=True)
    print(
        "总数 {total}，已有缓存 {cached}，文本可用 {skipped_text}，新增 OCR {ocr}，解析缓存 {parsed}，失败 {failed}，耗时 {elapsed:.1f}s".format(
            elapsed=elapsed,
            **stats,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
