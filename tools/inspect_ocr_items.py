from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import schedule_core


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: inspect_ocr_items.py <pdf_path> <kind> <limit>")
        return 2
    pdf_path = Path(sys.argv[1])
    source = {"name": pdf_path.name, "path": str(pdf_path), "kind": sys.argv[2], "content": b""}
    limit = int(sys.argv[3])
    items = schedule_core._extract_pdf_ocr_items(source)  # noqa: SLF001 - diagnostic tool.
    print(f"items={len(items)}")
    for item in items[:limit]:
        print(
            "{page} {x0:.1f},{y0:.1f} {x1:.1f},{y1:.1f} {text}".format(
                page=item.get("page"),
                x0=float(item.get("x0", 0)),
                y0=float(item.get("y0", 0)),
                x1=float(item.get("x1", 0)),
                y1=float(item.get("y1", 0)),
                text=item.get("text", ""),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
