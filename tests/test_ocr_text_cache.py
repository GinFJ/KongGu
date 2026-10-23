import json

from core import schedule_core


def _source() -> dict:
    return {
        "file_name": "成员甲-英方课表.pdf",
        "kind": "英方",
        "bytes": b"%PDF-fake-content",
        "source_path": "D:/fake/成员甲-英方课表.pdf",
    }


def _write_meta(path, *, engine: str, model: str) -> None:
    schedule_core._ocr_text_cache_meta_path(path).write_text(
        json.dumps(
            {"version": schedule_core.OCR_TEXT_CACHE_VERSION, "ocr_engine": engine, "ocr_model": model}
        ),
        encoding="utf-8",
    )


def test_ocr_text_cache_hits_same_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_TEXT_CACHE", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        schedule_core,
        "_extract_pdf_ocr_items",
        lambda *args, **kwargs: calls.append(1) or [],
    )
    source = _source()
    cache_path = schedule_core._ocr_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("已缓存的识别文字", encoding="utf-8")
    _write_meta(cache_path, engine=schedule_core.DEFAULT_OCR_ENGINE, model=schedule_core.DEFAULT_OCR_MODEL)

    text = schedule_core._extract_pdf_ocr_text(source)

    assert text == "已缓存的识别文字"
    assert calls == []


def test_ocr_layout_cache_ignores_parser_signature(monkeypatch, tmp_path):
    """改解析规则不能让全部图片课表重新 OCR 一遍。"""

    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path))
    source = _source()
    cache_path = schedule_core._ocr_layout_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    items = [{"page": 0, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0, "text": "课程"}]
    cache_path.write_text(
        json.dumps(
            {
                "version": schedule_core.OCR_LAYOUT_CACHE_VERSION,
                "parser_signature": "old-parser-signature",
                "items": items,
            }
        ),
        encoding="utf-8",
    )

    assert schedule_core._extract_pdf_ocr_items(source) == items


def test_ocr_layout_cache_uses_ocr_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_LAYOUT_CACHE", str(tmp_path))
    source = _source()
    cache_path = schedule_core._ocr_layout_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    items = [{"page": 0, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0, "text": "课程"}]
    cache_path.write_text(
        json.dumps(
            {
                "version": schedule_core.OCR_LAYOUT_CACHE_VERSION,
                "ocr_signature": schedule_core._ocr_cache_signature(),
                "items": items,
            }
        ),
        encoding="utf-8",
    )

    assert schedule_core._extract_pdf_ocr_items(source) == items


def test_ocr_text_cache_without_meta_is_treated_as_current_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_TEXT_CACHE", str(tmp_path))
    calls = []
    monkeypatch.setattr(
        schedule_core,
        "_extract_pdf_ocr_items",
        lambda *args, **kwargs: calls.append(1) or [],
    )
    source = _source()
    cache_path = schedule_core._ocr_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("旧版缓存文字", encoding="utf-8")

    text = schedule_core._extract_pdf_ocr_text(source)

    assert text == "旧版缓存文字"
    assert calls == []
    assert schedule_core._ocr_text_cache_meta_path(cache_path).exists()


def test_ocr_text_cache_rejects_other_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("KONGGU_OCR_TEXT_CACHE", str(tmp_path))
    calls = []

    def fake_items(source, *, progress=None):
        calls.append(1)
        return [{"page": 0, "text": "重新识别", "y0": 1, "x0": 1}]

    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_items", fake_items)
    source = _source()
    cache_path = schedule_core._ocr_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("旧引擎识别结果", encoding="utf-8")
    _write_meta(cache_path, engine="paddle_v6_small", model="PP-OCRv6-small")

    text = schedule_core._extract_pdf_ocr_text(source)

    assert text == "重新识别"
    assert calls == [1]
    meta = json.loads(schedule_core._ocr_text_cache_meta_path(cache_path).read_text(encoding="utf-8"))
    assert meta["ocr_engine"] == schedule_core.DEFAULT_OCR_ENGINE
    assert meta["ocr_model"] == schedule_core.DEFAULT_OCR_MODEL


def test_ocr_text_cache_ignores_empty_cached_text_and_retries(monkeypatch, tmp_path):
    """An empty cached text must not permanently disable OCR for the source."""

    monkeypatch.setenv("KONGGU_OCR_TEXT_CACHE", str(tmp_path))
    calls = []

    def fake_items(source, *, progress=None):
        calls.append(1)
        return [{"page": 0, "text": "重新识别", "y0": 1, "x0": 1}]

    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_items", fake_items)
    source = _source()
    cache_path = schedule_core._ocr_cache_path(source)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("", encoding="utf-8")

    text = schedule_core._extract_pdf_ocr_text(source)

    assert text == "重新识别"
    assert calls == [1]
    assert cache_path.read_text(encoding="utf-8") == "重新识别"


def test_ocr_text_cache_does_not_persist_empty_ocr_result(monkeypatch, tmp_path):
    """A failed/empty OCR run must be retried on the next attempt."""

    monkeypatch.setenv("KONGGU_OCR_TEXT_CACHE", str(tmp_path))
    monkeypatch.setattr(schedule_core, "_extract_pdf_ocr_items", lambda *args, **kwargs: [])
    source = _source()

    assert schedule_core._extract_pdf_ocr_text(source) == ""
    cache_path = schedule_core._ocr_cache_path(source)
    assert not cache_path.exists()
    assert not schedule_core._ocr_text_cache_meta_path(cache_path).exists()

    monkeypatch.setattr(
        schedule_core,
        "_extract_pdf_ocr_items",
        lambda *args, **kwargs: [{"page": 0, "text": "恢复识别", "y0": 1, "x0": 1}],
    )

    assert schedule_core._extract_pdf_ocr_text(source) == "恢复识别"
    assert cache_path.exists()
