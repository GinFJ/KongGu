from core.legacy_adapter import (
    build_file_records,
    build_member_schedules,
    build_process_result,
    course_blocks_from_legacy,
    infer_member_name_from_filename,
    legacy_source_to_model,
    model_source_to_legacy,
)
from core.models import PdfSource


def test_pdf_source_legacy_roundtrip():
    source = PdfSource(
        file_name="办公室-张三-中方课表.pdf",
        kind="中方",
        source_path="D:/fake/办公室-张三-中方课表.pdf",
        content_hash="abc",
        bytes_data=b"pdf",
    )

    legacy = model_source_to_legacy(source)
    restored = legacy_source_to_model(legacy)

    assert legacy["kind"] == "中方"
    assert legacy["bytes"] == b"pdf"
    assert restored.file_name == source.file_name
    assert restored.kind == "中方"


def test_build_process_result_from_legacy_blocks():
    legacy_blocks = [
        {
            "name": "张三",
            "source_type": "中方",
            "week": 3,
            "weekday": "周一",
            "periods": [1, 2],
            "source_file": "办公室-张三-中方课表.pdf",
        },
        {
            "name": "张三",
            "source_type": "英方",
            "week": 3,
            "weekday": "周三",
            "periods": [9],
            "source_file": "办公室-张三-英方课表.pdf",
        },
    ]
    blocks = course_blocks_from_legacy(legacy_blocks)
    members = build_member_schedules(blocks, ["张三"])
    sources = [
        PdfSource("办公室-张三-中方课表.pdf", "中方", "D:/fake/cn.pdf"),
        PdfSource("办公室-张三-英方课表.pdf", "英方", "D:/fake/uk.pdf"),
    ]
    records = build_file_records(sources, blocks, [])
    result = build_process_result(blocks=blocks, members=members, file_records=records)

    assert len(result.blocks) == 2
    assert result.members[0].status == "完整"
    assert result.summary.member_count == 1
    assert result.summary.complete_member_count == 1
    assert result.file_records[0].status == "已识别"


def test_file_records_match_by_inferred_member_name_when_source_file_missing():
    blocks = course_blocks_from_legacy(
        [
            {
                "name": "张三",
                "source_type": "中方",
                "week": 3,
                "weekday": "周一",
                "periods": [1],
            },
            {
                "name": "李四",
                "source_type": "中方",
                "week": 3,
                "weekday": "周二",
                "periods": [2],
            },
        ]
    )
    records = build_file_records(
        [PdfSource("办公室-张三-部长-中方课表.pdf", "中方", "D:/fake.pdf")],
        blocks,
        [],
    )

    assert infer_member_name_from_filename("办公室-张三-部长-中方课表.pdf") == "张三"
    assert records[0].member_name == "张三"
    assert records[0].block_count == 1


def test_infer_member_name_handles_irregular_filename_parts():
    assert infer_member_name_from_filename("2026春_办公室_张三_中方课表.pdf") == "张三"
    assert infer_member_name_from_filename("办公室张三中方课表.pdf") == "张三"
    assert infer_member_name_from_filename("宣传部-李四-英方.pdf") == "李四"


def test_build_member_schedules_flags_period_conflicts():
    blocks = course_blocks_from_legacy(
        [
            {
                "name": "张三",
                "source_type": "中方",
                "week": 3,
                "weekday": "周一",
                "periods": [1],
                "course": "高等数学",
                "source_file": "张三-中方课表.pdf",
            },
            {
                "name": "张三",
                "source_type": "英方",
                "week": 3,
                "weekday": "周一",
                "periods": [1],
                "course": "Academic Writing",
                "source_file": "张三-英方课表.pdf",
            },
        ]
    )

    members = build_member_schedules(blocks, ["张三"])

    assert members[0].status == "完整"
    assert len(members[0].errors) == 1
    assert "第3周周一第1节" in members[0].display_remark

    result = build_process_result(blocks=blocks, members=members, file_records=[])
    assert result.summary.complete_member_count == 0
    assert result.summary.pending_member_count == 1
