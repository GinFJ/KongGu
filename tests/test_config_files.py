import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_period_time_config_has_eleven_periods():
    config = json.loads((ROOT / "config" / "period_time.json").read_text(encoding="utf-8"))
    periods = config["periods"]

    assert len(periods) == 11
    assert [item["period"] for item in periods] == list(range(1, 12))
    for item in periods:
        assert {"period", "start", "end", "label"} <= set(item)
        assert item["start"]
        assert item["end"]
    assert [(item["start"], item["end"]) for item in periods] == [
        ("08:10", "08:55"),
        ("09:00", "09:45"),
        ("10:15", "11:00"),
        ("11:05", "11:50"),
        ("14:30", "15:15"),
        ("15:20", "16:05"),
        ("16:25", "17:10"),
        ("17:15", "18:00"),
        ("19:10", "19:55"),
        ("20:00", "20:45"),
        ("20:50", "21:35"),
    ]


def test_school_calendar_config_shape():
    config = json.loads((ROOT / "config" / "school_calendar.json").read_text(encoding="utf-8"))

    assert config["semester_start_date"] == "2026-03-02"
    assert config["teaching_weeks"] == 18
    assert len(config["weekdays"]) == 7
    assert isinstance(config["special_adjustments"], list)


def test_reference_library_config_shape():
    config = json.loads((ROOT / "config" / "reference_library.json").read_text(encoding="utf-8"))

    assert config["name"]
    assert config["root_path"]
    assert not Path(config["root_path"]).is_absolute()
    assert config["expected_pdf_count"] == 76
    assert config["expected_chinese_pdf_count"] == 38
    assert config["expected_english_pdf_count"] == 38


def test_ocr_model_config_shape():
    config = json.loads((ROOT / "config" / "ocr_models.json").read_text(encoding="utf-8"))

    names = {model["name"] for model in config["models"]}
    assert {"PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec"}.issubset(names)
    for model in config["models"]:
        assert model["url"].startswith("https://")
        assert model["url"].endswith(".tar")
