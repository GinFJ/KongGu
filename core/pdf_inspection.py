"""Local PDF structure inspection used before timetable parsing.

The primary engine is Firecrawl's ``pdf-inspector`` Python binding.  The
adapter deliberately exposes only diagnostic metadata: it does not decide
Konggu's quality state and it never stores extracted timetable text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from core.models import PdfSource


InspectionStatus = Literal["ready", "skipped", "unavailable"]
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 100


@dataclass(slots=True)
class PageOcrReason:
    """One 1-indexed PDF page and the reasons it may need OCR."""

    page: int
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PdfInspectionResult:
    """Privacy-safe structural facts about one source PDF."""

    source_file: str
    source_hash: str = ""
    status: InspectionStatus = "ready"
    engine: str = ""
    engine_version: str = ""
    pdf_type: str = "unknown"
    confidence: float = 0.0
    page_count: int = 0
    pages_needing_ocr: list[int] = field(default_factory=list)
    ocr_reasons_by_page: list[PageOcrReason] = field(default_factory=list)
    has_encoding_issues: bool = False
    is_complex_layout: bool = False
    pages_with_tables: list[int] = field(default_factory=list)
    pages_with_columns: list[int] = field(default_factory=list)
    processing_time_ms: int = 0
    warning: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_pdf_sources(sources: list[PdfSource]) -> list[dict[str, Any]]:
    """Inspect sources independently so one malformed PDF cannot hide others."""

    return [inspect_pdf_source(source).as_dict() for source in sources]


def inspect_pdf_source(source: PdfSource) -> PdfInspectionResult:
    """Inspect one PDF locally, preferring pdf-inspector with PyMuPDF fallback."""

    base = PdfInspectionResult(
        source_file=source.file_name,
        source_hash=str(source.content_hash or ""),
    )
    try:
        page_count = _preflight_pdf(source)
    except _InspectionLimitError as exc:
        base.status = "skipped"
        base.error = str(exc)
        return base
    except Exception as exc:
        base.status = "unavailable"
        base.error = f"PDF 结构检查失败：{type(exc).__name__}"
        return base

    if page_count > MAX_PDF_PAGES:
        base.status = "skipped"
        base.page_count = page_count
        base.error = f"PDF 共 {page_count} 页，超过结构检查上限 {MAX_PDF_PAGES} 页。"
        return base

    try:
        binding = _load_pdf_inspector()
        result = (
            binding.detect_pdf_bytes(source.bytes_data)
            if source.bytes_data is not None
            else binding.detect_pdf(source.source_path)
        )
        return _from_pdf_inspector(source, result)
    except Exception as exc:
        fallback = _inspect_with_pymupdf(source)
        if fallback.status == "ready":
            fallback.warning = f"pdf-inspector 不可用，已回退 PyMuPDF：{type(exc).__name__}"
        return fallback


def _load_pdf_inspector() -> Any:
    import pdf_inspector

    return pdf_inspector


def _from_pdf_inspector(source: PdfSource, result: Any) -> PdfInspectionResult:
    reasons: list[PageOcrReason] = []
    raw_reasons = getattr(result, "ocr_reasons_by_page", []) or []
    if isinstance(raw_reasons, dict):
        reasons = [
            PageOcrReason(page=int(page), reasons=[str(item) for item in values])
            for page, values in raw_reasons.items()
        ]
    else:
        for item in raw_reasons:
            reasons.append(
                PageOcrReason(
                    page=int(getattr(item, "page", 0) or 0),
                    reasons=[str(value) for value in (getattr(item, "reasons", []) or [])],
                )
            )
    try:
        engine_version = metadata.version("pdf-inspector")
    except metadata.PackageNotFoundError:
        engine_version = "unknown"
    return PdfInspectionResult(
        source_file=source.file_name,
        source_hash=str(source.content_hash or ""),
        status="ready",
        engine="firecrawl_pdf_inspector",
        engine_version=engine_version,
        pdf_type=str(getattr(result, "pdf_type", "unknown") or "unknown"),
        confidence=round(float(getattr(result, "confidence", 0.0) or 0.0), 4),
        page_count=int(getattr(result, "page_count", 0) or 0),
        pages_needing_ocr=sorted({int(page) for page in (getattr(result, "pages_needing_ocr", []) or [])}),
        ocr_reasons_by_page=sorted(reasons, key=lambda item: item.page),
        has_encoding_issues=bool(getattr(result, "has_encoding_issues", False)),
        is_complex_layout=bool(getattr(result, "is_complex_layout", False)),
        pages_with_tables=sorted({int(page) for page in (getattr(result, "pages_with_tables", []) or [])}),
        pages_with_columns=sorted({int(page) for page in (getattr(result, "pages_with_columns", []) or [])}),
        processing_time_ms=int(getattr(result, "processing_time_ms", 0) or 0),
    )


def _preflight_pdf(source: PdfSource) -> int:
    size = len(source.bytes_data) if source.bytes_data is not None else Path(source.source_path).stat().st_size
    if size > MAX_PDF_BYTES:
        raise _InspectionLimitError(
            f"PDF 大小为 {size / 1024 / 1024:.1f} MB，超过结构检查上限 {MAX_PDF_BYTES // 1024 // 1024} MB。"
        )
    import fitz

    document = (
        fitz.open(stream=source.bytes_data, filetype="pdf")
        if source.bytes_data is not None
        else fitz.open(source.source_path)
    )
    try:
        if document.needs_pass:
            raise ValueError("PDF 已加密，无法在未提供密码时检查。")
        return int(document.page_count)
    finally:
        document.close()


def _inspect_with_pymupdf(source: PdfSource) -> PdfInspectionResult:
    """Reduced fallback when the native binding cannot be loaded or executed."""

    try:
        import fitz

        document = (
            fitz.open(stream=source.bytes_data, filetype="pdf")
            if source.bytes_data is not None
            else fitz.open(source.source_path)
        )
        pages_needing_ocr: list[int] = []
        page_reasons: list[PageOcrReason] = []
        full_page_image_pages = 0
        pages_with_text = 0
        encoding_issue = False
        try:
            for page_index, page in enumerate(document):
                text = str(page.get_text("text") or "")
                compact = "".join(text.split())
                if compact:
                    pages_with_text += 1
                replacement_ratio = compact.count("\ufffd") / max(1, len(compact))
                bad_encoding = replacement_ratio >= 0.05
                encoding_issue = encoding_issue or bad_encoding
                image_coverage = _page_image_coverage(page)
                if image_coverage >= 0.5:
                    full_page_image_pages += 1
                reasons: list[str] = []
                if not compact:
                    reasons.append("scanned" if image_coverage >= 0.5 else "no_text")
                elif bad_encoding:
                    reasons.append("suspected_garbled_text")
                elif len(compact) < 20 and image_coverage >= 0.5:
                    reasons.extend(["sparse_text", "full_page_image"])
                if reasons:
                    page_number = page_index + 1
                    pages_needing_ocr.append(page_number)
                    page_reasons.append(PageOcrReason(page_number, reasons))
            page_count = int(document.page_count)
        finally:
            document.close()

        if not pages_needing_ocr:
            pdf_type = "text_based"
            confidence = 0.85
        elif len(pages_needing_ocr) < page_count:
            pdf_type = "mixed"
            confidence = 0.75
        elif pages_with_text == 0 and full_page_image_pages == page_count:
            pdf_type = "scanned"
            confidence = 0.85
        else:
            pdf_type = "image_based"
            confidence = 0.65
        return PdfInspectionResult(
            source_file=source.file_name,
            source_hash=str(source.content_hash or ""),
            status="ready",
            engine="pymupdf_fallback",
            engine_version=str(getattr(fitz, "VersionBind", "unknown")),
            pdf_type=pdf_type,
            confidence=confidence,
            page_count=page_count,
            pages_needing_ocr=pages_needing_ocr,
            ocr_reasons_by_page=page_reasons,
            has_encoding_issues=encoding_issue,
        )
    except Exception as exc:
        return PdfInspectionResult(
            source_file=source.file_name,
            source_hash=str(source.content_hash or ""),
            status="unavailable",
            error=f"PDF 结构检查失败：{type(exc).__name__}",
        )


def _page_image_coverage(page: Any) -> float:
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    covered = 0.0
    for info in page.get_image_info(xrefs=True):
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = (float(value) for value in bbox)
        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return min(1.0, covered / page_area)


class _InspectionLimitError(ValueError):
    pass
