# Copyright (c) 2025 PaddlePaddle Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0.
# Adapted from PaddleOCR v3.7.0 docs/version3.x/pipeline_usage/OCR.en.md.
# Local changes: fixed CPU models/configuration, bounded JSON adapter, no file output.
# See ../../third_party/PaddleOCR/NOTICE and LICENSE for upstream attribution.
"""SDK adapter. Model initialization happens once, before readiness is reported."""

from __future__ import annotations

import hashlib
import json
import math
from importlib.metadata import version
from numbers import Real
from pathlib import Path
from typing import Any

ENGINE_VERSION = "3.7.0"
MODEL = "PP-OCRv6_small_det+PP-OCRv6_small_rec@px3.7.2-pd3.3.1-det2048-line0-doc0-unwarp0-r1"
MAX_BLOCKS = 1200
MAX_TEXT_LENGTH = 2000
MAX_PAGE_CHARACTERS = 30000


class InvalidEngineResult(ValueError):
    """An OCR response cannot safely be used as location evidence."""


def _evidence_number(value: Any) -> float:
    """Accept native/NumPy real numbers, never coerce text or booleans."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidEngineResult("invalid OCR numeric evidence")
    return float(value)


def verify_model_files(model_name: str, directory: str | Path) -> None:
    """Fail closed if an upstream same-name model or mounted cache has changed."""
    manifest = json.loads(Path(__file__).with_name("model_manifest.json").read_text())
    for filename, expected in manifest[model_name].items():
        with (Path(directory) / filename).open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError("OCR model checksum does not match the pinned manifest")


def normalize_result(result: Any, *, page: int, width: int, height: int) -> dict[str, Any]:
    """PaddleX OCRResult is dict-like; do not serialize its input image/metadata."""
    try:
        texts = result["rec_texts"]
        scores = result["rec_scores"]
        polygons = result["rec_polys"]
        if not (len(texts) == len(scores) == len(polygons) <= MAX_BLOCKS):
            raise InvalidEngineResult("inconsistent OCR output")
        blocks = []
        characters = 0
        for index, (text, score, polygon) in enumerate(zip(texts, scores, polygons)):
            if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
                raise InvalidEngineResult("invalid OCR text")
            score = _evidence_number(score)
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise InvalidEngineResult("invalid OCR score")
            # The backend joins blocks with newline separators into a 30,000
            # character InvoicePage; count those separators before returning.
            characters += len(text) + bool(blocks)
            if characters > MAX_PAGE_CHARACTERS or len(polygon) != 4:
                raise InvalidEngineResult("oversized OCR output")
            points = []
            for point in polygon:
                if len(point) != 2:
                    raise InvalidEngineResult("invalid OCR geometry")
                x, y = (_evidence_number(value) for value in point)
                if not (
                    math.isfinite(x) and math.isfinite(y) and 0 <= x <= width and 0 <= y <= height
                ):
                    raise InvalidEngineResult("OCR coordinates outside input image")
                points.append([x, y])
            area = sum(
                points[i][0] * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * points[i][1]
                for i in range(4)
            )
            if abs(area) < 1:
                raise InvalidEngineResult("empty OCR geometry")
            if not text.strip():
                continue
            blocks.append(
                {"id": f"p{page}-b{index + 1}", "text": text, "score": score, "polygon": points}
            )
        return {"page": page, "width": width, "height": height, "blocks": blocks}
    except (KeyError, TypeError, OverflowError) as exc:
        raise InvalidEngineResult("invalid OCR output") from exc


class PaddleEngine:
    engine_version = ENGINE_VERSION
    model = MODEL

    def __init__(self) -> None:
        for package, expected in (
            ("paddleocr", "3.7.0"),
            ("paddlex", "3.7.2"),
            ("paddlepaddle", "3.3.1"),
        ):
            if version(package) != expected:
                raise RuntimeError("PaddleOCR runtime version does not match pinned model")
        from paddleocr import PaddleOCR
        from paddlex.inference.utils.official_models import official_models

        detection_dir = official_models["PP-OCRv6_small_det"]
        recognition_dir = official_models["PP-OCRv6_small_rec"]
        verify_model_files("PP-OCRv6_small_det", detection_dir)
        verify_model_files("PP-OCRv6_small_rec", recognition_dir)

        self._pipeline = PaddleOCR(
            text_detection_model_name="PP-OCRv6_small_det",
            text_detection_model_dir=str(detection_dir),
            text_recognition_model_name="PP-OCRv6_small_rec",
            text_recognition_model_dir=str(recognition_dir),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
            cpu_threads=2,
            enable_mkldnn=False,
            text_det_limit_side_len=2048,
            text_det_limit_type="max",
            text_rec_score_thresh=0.0,
        )
        import numpy as np

        # Run once at startup so readiness includes actual predictor construction.
        self._pipeline.predict(np.full((64, 256, 3), 255, dtype=np.uint8))

    def recognize(self, image: Any, page: int) -> dict[str, Any]:
        import numpy as np

        # Paddle image arrays are BGR. No page rotation or unwarping is performed;
        # rec_polys therefore refer to exactly the caller's input image pixels.
        pixels = np.array(image)[:, :, ::-1].copy()
        results = self._pipeline.predict(pixels)
        if len(results) != 1:
            raise InvalidEngineResult("expected exactly one OCR result")
        return normalize_result(results[0], page=page, width=image.width, height=image.height)
