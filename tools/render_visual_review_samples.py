from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import fitz


def safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", text).strip("_")[:90]


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: render_visual_review_samples.py <archive_root> <audit_csv> <out_dir>")
        return 2

    archive_root = Path(sys.argv[1])
    audit_csv = Path(sys.argv[2])
    out_dir = Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(audit_csv.open(encoding="utf-8-sig")))
    review_rows = [row for row in rows if row.get("status") != "auto_accepted"]
    rendered: list[dict[str, str]] = []

    for row in review_rows:
        pdf_path = archive_root / row["relative_path"]
        stem = f"{int(row['index']):02d}_{row['kind']}_{row['name']}_{safe_name(Path(row['relative_path']).stem)}"
        doc = fitz.open(pdf_path)
        try:
            for page_index, page in enumerate(doc):
                pix = page.get_pixmap(dpi=180, alpha=False)
                image_path = out_dir / f"{stem}_p{page_index + 1}.png"
                pix.save(image_path)
                rendered.append(
                    {
                        "index": row["index"],
                        "kind": row["kind"],
                        "name": row["name"],
                        "status": row["status"],
                        "page": str(page_index + 1),
                        "pdf": str(pdf_path),
                        "image": str(image_path.resolve()),
                    }
                )
        finally:
            doc.close()

    manifest_path = out_dir / "visual_review_manifest.csv"
    if rendered:
        with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rendered[0].keys()))
            writer.writeheader()
            writer.writerows(rendered)

    report_lines = ["# 视觉复核样本", ""]
    for item in rendered:
        report_lines.append(
            f"- {item['index']} {item['kind']} {item['name']} p{item['page']} "
            f"`{Path(item['image']).name}`"
        )
    (out_dir / "visual_review_manifest.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(manifest_path.resolve())
    print(len(rendered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
