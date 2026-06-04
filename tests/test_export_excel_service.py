import pandas as pd

from app.services.export_excel_service import ExcelExportRequest, build_export_excel_bytes


class FakeExportCore:
    def __init__(self):
        self.kwargs = None

    def build_empty_schedule_excel_bytes(self, **kwargs):
        self.kwargs = kwargs
        return b"xlsx-bytes"


def test_build_export_excel_bytes_delegates_to_schedule_core():
    core = FakeExportCore()
    calendar_df = pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])
    timetable_df = pd.DataFrame([{"period": 1, "start": "08:10", "end": "08:55"}])
    blocks_df = pd.DataFrame([{"name": "张三", "week": 3}])
    all_slot_df = pd.DataFrame([{"week": 3, "free_members": "李四"}])

    data = build_export_excel_bytes(
        schedule_core=core,
        request=ExcelExportRequest(
            occupancy={(3, "周一", 1): {"张三"}},
            students=["张三", "李四"],
            weeks=[3],
            calendar_df=calendar_df,
            timetable_df=timetable_df,
            blocks_df=blocks_df,
            all_slot_df=all_slot_df,
            threshold=0,
        ),
    )

    assert data == b"xlsx-bytes"
    assert core.kwargs["students"] == ["张三", "李四"]
    assert core.kwargs["weeks"] == [3]
    assert core.kwargs["calendar_df"] is calendar_df
    assert core.kwargs["timetable_df"] is timetable_df
    assert core.kwargs["blocks_df"] is blocks_df
    assert core.kwargs["all_slot_df"] is all_slot_df
    assert core.kwargs["threshold"] == 0
