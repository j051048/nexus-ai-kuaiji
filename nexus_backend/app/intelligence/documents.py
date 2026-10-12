"""Private byte-only preparation; PDF native processing is isolated and time bounded."""

import base64
from io import BytesIO
import json
import math
from pathlib import Path
import subprocess
import sys
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import PreparedPage
from .provider import MAX_INLINE_IMAGE_BYTES, ProviderError


MAX_DOCUMENT_BYTES = 20 * 1_048_576
MAX_PIXELS = 20_000_000
MAX_LONG_EDGE = 2000
MAX_PAGES = 6
MAX_PAGE_TEXT = 30_000
PDF_TIMEOUT_SECONDS = 30
ALLOWED_IMAGE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class DocumentError(ProviderError):
    def __init__(self, code: str, detail: str):
        super().__init__(code, detail, status=422)


def _image_data_url(image: Image.Image) -> str:
    # Fresh pixels prevent EXIF, ICC and other uploaded metadata from reaching the supplier.
    original = image
    image = ImageOps.exif_transpose(original)
    original.close()
    image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
    rgb = image.convert("RGB")
    clean = Image.new("RGB", rgb.size, (255, 255, 255))
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        clean.paste(rgba, mask=rgba.getchannel("A"))
        rgba.close()
    else:
        clean.paste(rgb)
    rgb.close()
    encoded = BytesIO()
    try:
        clean.save(encoded, format="JPEG", quality=90, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
    finally:
        clean.close()
        encoded.close()
        image.close()


def _check_total(pages: list[PreparedPage]) -> None:
    total = sum((len(page.image_data_url.split(",", 1)[1]) * 3 // 4) for page in pages)
    if total > MAX_INLINE_IMAGE_BYTES:
        raise DocumentError("document_too_large", "票据渲染图像总量超过 12 MiB，请人工处理")


def _prepare_image(data: bytes, media_type: str) -> list[PreparedPage]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as picture:
                if picture.format != ALLOWED_IMAGE_FORMATS[media_type] or getattr(picture, "n_frames", 1) != 1:
                    raise DocumentError("document_invalid", "票据图像格式不匹配或包含多个帧，请人工处理")
                if picture.width <= 0 or picture.height <= 0 or picture.width * picture.height > MAX_PIXELS:
                    raise DocumentError("document_too_large", "票据图像超过 2000 万像素，请人工处理")
                picture.load()
                page = PreparedPage(page=1, image_data_url=_image_data_url(picture.copy()))
        pages = [page]
        _check_total(pages)
        return pages
    except DocumentError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DocumentError("document_too_large", "票据图像超过安全处理范围，请人工处理") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise DocumentError("document_invalid", "票据图像损坏或无法读取，请人工处理") from None


def _prepare_pdf_in_worker(data: bytes) -> list[PreparedPage]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise DocumentError("document_unavailable", "尚未安装 PDF 识别准备依赖，请人工处理") from None
    document = None
    try:
        document = pdfium.PdfDocument(data)
        if not 1 <= len(document) <= MAX_PAGES:
            raise DocumentError("document_page_limit", "PDF 必须为 1 至 6 页；超限票据须人工处理")
        pages = []
        for index in range(len(document)):
            page = textpage = bitmap = None
            try:
                page = document[index]
                width, height = page.get_size()
                if not all(math.isfinite(dimension) and 0 < dimension <= 20_000 for dimension in (width, height)):
                    raise DocumentError("document_too_large", "PDF 页面尺寸超出安全范围，请人工处理")
                scale = min(200 / 72, MAX_LONG_EDGE / max(width, height))
                if math.ceil(width * scale) * math.ceil(height * scale) > MAX_PIXELS:
                    raise DocumentError("document_too_large", "PDF 页面像素超过安全范围，请人工处理")
                textpage = page.get_textpage()
                if textpage.count_chars() > MAX_PAGE_TEXT:
                    raise DocumentError("document_too_large", "PDF 页面文字超过识别限制，请人工处理")
                native_text = textpage.get_text_range()
                if len(native_text) > MAX_PAGE_TEXT:
                    raise DocumentError("document_too_large", "PDF 页面文字超过识别限制，请人工处理")
                bitmap = page.render(scale=scale)
                rendered = bitmap.to_pil()
                try:
                    data_url = _image_data_url(rendered.copy())
                finally:
                    rendered.close()
                pages.append(PreparedPage(page=index + 1, image_data_url=data_url, native_text=native_text))
                _check_total(pages)
            finally:
                for resource in (bitmap, textpage, page):
                    if resource is not None:
                        resource.close()
        return pages
    except DocumentError:
        raise
    except Exception:
        # Native errors may include filenames or raw document details. They never leave this boundary.
        raise DocumentError("document_invalid", "PDF 损坏、加密或无法读取，请人工处理") from None
    finally:
        if document is not None:
            document.close()


def _prepare_pdf(data: bytes) -> list[PreparedPage]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.intelligence.documents", "--pdf-worker"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2], timeout=PDF_TIMEOUT_SECONDS,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise DocumentError("document_timeout", "PDF 处理超时，请人工处理") from None
    except OSError:
        raise DocumentError("document_unavailable", "PDF 处理暂时不可用，请人工处理") from None
    if result.returncode != 0 or len(result.stdout) > MAX_INLINE_IMAGE_BYTES * 4 // 3 + 1_000_000:
        raise DocumentError("document_invalid", "PDF 处理失败，请人工处理")
    try:
        payload = json.loads(result.stdout)
        if payload.get("error"):
            raise DocumentError(payload["error"]["code"], payload["error"]["detail"])
        pages = [PreparedPage(**value) for value in payload["pages"]]
        if not 1 <= len(pages) <= MAX_PAGES:
            raise ValueError
        _check_total(pages)
        return pages
    except DocumentError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DocumentError("document_invalid", "PDF 处理失败，请人工处理") from None


def prepare_document(data: bytes, media_type: str) -> list[PreparedPage]:
    if not isinstance(data, bytes) or not data:
        raise DocumentError("document_invalid", "票据附件为空或格式无效")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError("document_too_large", "票据附件超过安全处理大小限制")
    if media_type in ALLOWED_IMAGE_FORMATS:
        return _prepare_image(data, media_type)
    if media_type == "application/pdf" and data.startswith(b"%PDF-"):
        return _prepare_pdf(data)
    raise DocumentError("document_unsupported", "仅支持单帧 JPEG、PNG、WebP 图像及最多 6 页 PDF")


if __name__ == "__main__" and sys.argv[1:] == ["--pdf-worker"]:
    try:
        worker_data = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
        if len(worker_data) > MAX_DOCUMENT_BYTES:
            raise DocumentError("document_too_large", "票据附件超过安全处理大小限制")
        worker_pages = _prepare_pdf_in_worker(worker_data)
        worker_payload = {"pages": [
            {"page": page.page, "image_data_url": page.image_data_url, "native_text": page.native_text}
            for page in worker_pages
        ]}
    except DocumentError as error:
        worker_payload = {"error": {"code": error.code, "detail": error.detail}}
    sys.stdout.buffer.write(json.dumps(worker_payload, ensure_ascii=False).encode("utf-8"))
