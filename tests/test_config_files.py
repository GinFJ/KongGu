import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_period_time_config_has_configured_periods():
    config = json.loads((ROOT / "config" / "period_time.json").read_text(encoding="utf-8"))
    periods = config["periods"]

    assert len(periods) == 13
    assert [item["period"] for item in periods] == [1, 2, 3, 4, 12, 13, 5, 6, 7, 8, 9, 10, 11]
    for item in periods:
        assert {"period", "start", "end", "label"} <= set(item)
        assert item["start"]
        assert item["end"]
    assert [(item["start"], item["end"]) for item in periods] == [
        ("08:10", "08:55"),
        ("09:00", "09:45"),
        ("10:15", "11:00"),
        ("11:05", "11:50"),
        ("12:40", "13:25"),
        ("13:30", "14:15"),
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
    # root_path may be absolute (external library) or relative; both are valid
    assert Path(config["root_path"]).exists() or not Path(config["root_path"]).is_absolute()
    assert config["expected_pdf_count"] == 70
    assert config["expected_chinese_pdf_count"] == 35
    assert config["expected_english_pdf_count"] == 35


def test_ocr_model_config_shape():
    config = json.loads((ROOT / "config" / "ocr_models.json").read_text(encoding="utf-8"))

    names = {model["name"] for model in config["models"]}
    assert {"PP-OCRv4_mobile_det", "PP-OCRv4_mobile_rec"}.issubset(names)
    for model in config["models"]:
        assert model["url"].startswith("https://")
        assert model["url"].endswith(".tar")
        assert len(model["archive_sha256"]) == 64
        assert model["archive_size"] > 0
        assert model["target_dir"].startswith("ocr_models/")
