import pandas as pd

from app.services.generate_availability_service import generate_availability
from core.models import PdfSource


class FakeScheduleCore:
    def __init__(self):
        self.loaded_root = None

    def local_pdf_sources_from_dir(self, root):
        self.loaded_root = root
        return [
            {
                "file_name": "办公室-张三-中方课表.pdf",
                "kind": "中方",
                "source_path": "D:/fake/办公室-张三-中方课表.pdf",
                "bytes": b"pdf",
            }
        ]

    def parse_actual_pdf_sources(self, sources, uploaded_calendar_df=None):
        assert uploaded_calendar_df is None
        blocks = [
            {
                "name": "张三",
                "source": "中方",
                "source_type": "中方",
                "kind": "中方",
                "week": 3,
                "date": "2026-03-16",
                "weekday": "周一",
                "period": 1,
                "periods": [1],
                "time": "08:10-08:55",
                "course": "高等数学",
                "source_file": sources[0]["file_name"],
            }
        ]
        calendar = pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])
        preview = pd.DataFrame(blocks)
        return blocks, calendar, [], preview

    def synthesize_calendar_from_blocks(self, blocks):
        return pd.DataFrame([{"date": "2026-03-16", "week": 3, "weekday": "周一"}])

    def build_occupancy(self, blocks):
        return {(3, "周一", 1): {"张三"}}

    def build_slot_table(self, occupancy, students, weeks, weekdays, periods):
        return pd.DataFrame(
            [
                {
                    "week": 3,
                    "date": "2026-03-16",
                    "weekday": "周一",
                    "period": 1,
                    "time": "08:10-08:55",
                    "free_count": 0,
                    "free_members": "",
                    "occupied_count": 1,
                    "occupied_members": "张三",
                }
            ]
        )

    def blocks_to_dataframe(self, blocks):
        return pd.DataFrame(blocks)


def test_generate_availability_from_selected_sources():
    source = PdfSource("办公室-张三-中方课表.pdf", "中方", "D:/fake.pdf", bytes_data=b"pdf")
    result = generate_availability(
        schedule_core=FakeScheduleCore(),
        selected_sources=[source],
        root_dir="",
        weekdays=["周一"],
    )

    assert result.model_sources == [source]
    assert result.students == ["张三"]
    assert result.weeks == [3]
    assert len(result.blocks) == 1
    assert result.member_schedules[0].status == "缺英方"
    assert result.file_records[0].status == "已识别"


def test_generate_availability_discovers_sources_from_root_dir():
    core = FakeScheduleCore()
    result = generate_availability(
        schedule_core=core,
        selected_sources=[],
        root_dir="D:/fake-root",
        weekdays=["周一"],
    )

    assert core.loaded_root == "D:/fake-root"
    assert result.model_sources[0].file_name == "办公室-张三-中方课表.pdf"
    assert result.occupancy[(3, "周一", 1)] == {"张三"}
