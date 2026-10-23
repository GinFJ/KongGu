"""导出物隐私回归：工作簿不得包含内部标识、几何或内容哈希。"""

from io import BytesIO

import pandas as pd
from openpyxl import load_workbook

from core import schedule_core

_ALLOWED_BLOCK_HEADERS = {"成员", "课表来源", "周次", "日期", "星期", "节次", "时间", "课程", "来源文件"}


def _export(**overrides) -> bytes:
    payload = dict(
        occupancy={(1, "周一", 1): {"甲｜活动部｜干事"}},
        students=["甲｜活动部｜干事", "乙｜办公室｜部长"],
        weeks=[1],
        calendar_df=pd.DataFrame(),
        timetable_df=schedule_core.default_timetable(),
        blocks_df=pd.DataFrame(
            [
                {
                    "name": "甲",
                    "source": "中方",
                    "week": 1,
                    "date": "2026-03-02",
                    "weekday": "周一",
                    "period": 1,
                    "time": "08:10-08:55",
                    "course": "课程",
                    "member_key": "甲｜活动部｜干事",
                    "department": "活动部",
                    "role": "干事",
                    "source_hash": "a" * 64,
                    "block_id": "b-1",
                    "periods": [1],
                    "text_source": "内嵌文本",
                    "bbox": [1.0, 2.0, 3.0, 4.0],
                    "page": 0,
                    "page_width": 595.0,
                    "page_height": 842.0,
                }
            ]
        ),
    )
    payload.update(overrides)
    return schedule_core.build_empty_schedule_excel_bytes(**payload)


def test_block_sheets_only_expose_user_columns():
    workbook = load_workbook(BytesIO(_export()))

    for sheet_name in ("课程占用明细", "成员课程明细"):
        header = [cell.value for cell in next(workbook[sheet_name].iter_rows(min_row=1, max_row=1))]
        assert set(header) <= _ALLOWED_BLOCK_HEADERS
        for internal in ("member_key", "source_hash", "block_id", "bbox", "page_width", "page_height", "periods"):
            assert internal not in header


def test_exported_workbook_has_no_content_hash_or_geometry_values():
    workbook = load_workbook(BytesIO(_export()))

    strings = [
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if isinstance(cell.value, str)
    ]
    hashes = [value for value in strings if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)]
    assert not hashes


def test_file_and_correction_sheets_do_not_export_raw_hashes():
    workbook = load_workbook(
        BytesIO(
            _export(
                file_df=pd.DataFrame([{"文件名": "a.pdf", "成员": "甲", "状态": "完成"}]),
                correction_df=pd.DataFrame(
                    [{"来源编号": "abcd1234", "课程块": "b-1", "字段": "course", "原因": "核对"}]
                ),
            )
        )
    )

    file_header = [cell.value for cell in next(workbook["文件处理记录"].iter_rows(min_row=1, max_row=1))]
    correction_header = [cell.value for cell in next(workbook["人工修正历史"].iter_rows(min_row=1, max_row=1))]
    assert "来源哈希" not in file_header
    assert "来源哈希" not in correction_header
    assert "课程块ID" not in correction_header
