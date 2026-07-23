"""OCR engine interface and offline deployment metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class OcrEngine(Protocol):
    engine_id: str
    model_version: str

    def recognize(self, image: Any) -> Any:
        """Recognize one page image without network access."""


@dataclass(frozen=True, slots=True)
class OcrEngineDescriptor:
    engine_id: str
    model_version: str
    stability: str
    runtime: str
    bundled_in_production: bool


ENGINE_DESCRIPTORS = {
    "paddle_v4": OcrEngineDescriptor(
        "paddle_v4", "PP-OCRv4-mobile", "stable", "paddlepaddle", True
    ),
    "paddle_v6_small": OcrEngineDescriptor(
        "paddle_v6_small", "PP-OCRv6-small", "experimental", "paddlepaddle", False
    ),
    "rapidocr_v6_onnx": OcrEngineDescriptor(
        "rapidocr_v6_onnx", "PP-OCRv6-mobile-onnx", "experimental", "onnxruntime", False
    ),
}


class PaddleV4Engine:
    engine_id = "paddle_v4"
    model_version = "PP-OCRv4-mobile"

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def recognize(self, image: Any) -> Any:
        try:
            return self.runtime.ocr(image) if hasattr(self.runtime, "ocr") else self.runtime.predict(image)
        except TypeError:
            return self.runtime.predict(image)

    def ocr(self, image: Any) -> Any:
        return self.recognize(image)

    def predict(self, image: Any) -> Any:
        return self.recognize(image)


def candidate_can_replace_default(
    *,
    default_f1: float,
    candidate_f1: float,
    default_seconds: float,
    candidate_seconds: float,
    default_package_bytes: int,
    candidate_package_bytes: int,
) -> bool:
    speed_improvement = (
        (default_seconds - candidate_seconds) / default_seconds if default_seconds > 0 else 0
    )
    size_improvement = (
        (default_package_bytes - candidate_package_bytes) / default_package_bytes
        if default_package_bytes > 0
        else 0
    )
    return candidate_f1 >= default_f1 and max(speed_improvement, size_improvement) >= 0.15
