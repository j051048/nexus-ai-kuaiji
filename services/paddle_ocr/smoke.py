"""Opt-in real-model smoke: python -m services.paddle_ocr.smoke --font FONT --output DIR."""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .app import RecognizeRequest, decode_image
from .engine import PaddleEngine


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recognize a synthetic Chinese invoice with the real pinned CPU models"
    )
    parser.add_argument("--font", required=True, help="Local Chinese TrueType/OpenType font")
    parser.add_argument(
        "--output",
        required=True,
        help="Ignored local output directory; never use real customer documents",
    )
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    page = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.truetype(args.font, 42)
    lines = [
        "测试专用虚构票据",
        "购买方：衡账演示有限公司",
        "销售方：虚构办公用品店",
        "价税合计：1234.56元",
        "金额：1164.68元  税额：69.88元",
        "开票日期：2026年10月14日",
    ]
    for index, line in enumerate(lines):
        draw.text((50, 35 + 95 * index), line, font=font, fill="black")
    page.save(output / "synthetic-invoice.png")
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    request = RecognizeRequest.model_validate(
        {
            "pages": [
                {
                    "page": 1,
                    "image_data_url": "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode(),
                }
            ]
        }
    )
    image, _ = decode_image(request.pages[0].image_data_url)
    started = time.monotonic()
    engine = PaddleEngine()
    ready = time.monotonic()
    result = engine.recognize(image, 1)
    text = "\n".join(block["text"] for block in result["blocks"])
    for required in ("1234.56", "1164.68", "69.88", "2026", "购买方", "销售方"):
        if required not in text:
            raise AssertionError(f"Synthetic invoice did not recognize {required}")
    receipt = {
        "synthetic_only": True,
        "engine_version": engine.engine_version,
        "model": engine.model,
        "startup_ms": round((ready - started) * 1000),
        "elapsed_ms": round((time.monotonic() - ready) * 1000),
        "page": result,
    }
    (output / "result.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    image.close()
    page.close()
    print(
        json.dumps(
            {
                "ok": True,
                "blocks": len(result["blocks"]),
                "result_path": str(output / "result.json"),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
