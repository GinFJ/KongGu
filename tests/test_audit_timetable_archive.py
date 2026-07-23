from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_audit_module():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "audit_timetable_archive.py"
    spec = importlib.util.spec_from_file_location("audit_timetable_archive", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_marks_same_content_different_names(tmp_path: Path):
    audit = _load_audit_module()
    first = tmp_path / "活动部-王婧琪-干事-中方课表.pdf"
    second = tmp_path / "活动部-苏筱羽-干事-中方课表.pdf"
    third = tmp_path / "活动部-唐洋-部长-英方课表.pdf"
    first.write_bytes(b"same-pdf-bytes")
    second.write_bytes(b"same-pdf-bytes")
    third.write_bytes(b"different-pdf-bytes")

    rows = audit.build_inventory(tmp_path)
    duplicates = [row for row in rows if "duplicate_content" in str(row["suspicious"])]

    assert len(duplicates) == 2
    assert {row["name"] for row in duplicates} == {"王婧琪", "苏筱羽"}
    assert all("duplicate_name_mismatch" in str(row["suspicious"]) for row in duplicates)
    assert len({row["duplicate_group"] for row in duplicates}) == 1
    assert audit.inventory_summary(rows)["duplicate_name_mismatch"] == 2


def test_brief_error_truncates_and_escapes_markdown_table_text():
    audit = _load_audit_module()

    text = audit.brief_error("第一段|第二段\n" + "很长" * 80, limit=20)

    assert "|" not in text
    assert "\n" not in text
    assert len(text) == 20
    assert text.endswith("...")


def test_classify_parse_status_rejects_placeholder_filename():
    audit = _load_audit_module()

    status = audit.classify_parse_status(
        {
            "kind": "中方",
            "blocks": 210,
            "errors": "",
            "suspicious": "filename_dummy",
        }
    )

    assert status == "needs_review_placeholder_filename"


def test_parse_inventory_preflight_rejects_duplicate_name_mismatch(monkeypatch):
    audit = _load_audit_module()

    def fail_if_parser_runs(_sources):
        raise AssertionError("parser should not run for known bad inventory rows")

    monkeypatch.setattr(audit.schedule_core, "parse_actual_pdf_sources", fail_if_parser_runs)
    rows = [
        {
            "path": "活动部-王婧琪-干事-中方课表.pdf",
            "relative_path": "活动部/干事/中方课表/活动部-王婧琪-干事-中方课表.pdf",
            "inferred_kind": "中方",
            "name": "王婧琪",
            "suspicious": "duplicate_content;duplicate_name_mismatch",
        }
    ]

    parsed = audit.parse_inventory(rows, offset=0, limit=0, skip_image=False, only_image=False)

    assert parsed[0]["status"] == "needs_review_duplicate_name_mismatch"
    assert parsed[0]["blocks"] == 0
    assert "同一 PDF 内容" in parsed[0]["errors"]
