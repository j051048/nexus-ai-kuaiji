import io

import pytest
from PIL import Image

from app.business.storage import AttachmentStore, MAX_FILE_BYTES, validate_attachment
from app.accounting.domain import AccountingError


def png():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    return stream.getvalue()


def test_private_opaque_key_hash_and_file_name_never_select_storage_path(tmp_path):
    store = AttachmentStore(tmp_path / "private")
    data = png()
    metadata = store.put("../../客户资料.png", "image/png", data)
    assert metadata["filename"] == "客户资料.png"
    assert "客户资料" not in metadata["storage_key"]
    assert store.read(metadata) == data
    assert len(metadata["sha256"]) == 64
    assert len(list((tmp_path / "private").rglob("*"))) == 2
    store.discard(metadata)
    assert not (tmp_path / "private" / metadata["storage_key"]).exists()


@pytest.mark.parametrize("name,mime,content", [
    ("file.html", "text/html", b"<script>alert(1)</script>"),
    ("invoice.pdf", "application/pdf", b"<html>not a pdf</html>"),
    ("invoice.exe", "image/png", png()),
    ("invoice.png", "application/pdf", png()),
    ("invoice.png", "image/png", b"\x89PNG\r\n\x1a\ntruncated"),
    ("invoice.pdf", "application/pdf", b"%PDF-1.4\ntruncated"),
    ("invoice.png", "image/png", b""),
])
def test_unusable_or_mismatched_content_is_rejected(name, mime, content):
    with pytest.raises(AccountingError):
        validate_attachment(name, mime, content)


def test_file_size_and_image_dimensions_are_bounded():
    with pytest.raises(AccountingError) as error:
        validate_attachment("invoice.pdf", "application/pdf", b"%PDF-" + b"x" * MAX_FILE_BYTES + b"%%EOF")
    assert error.value.status == 413
    stream = io.BytesIO()
    Image.new("1", (5000, 5000)).save(stream, format="PNG")
    with pytest.raises(AccountingError) as error:
        validate_attachment("large.png", "image/png", stream.getvalue())
    assert error.value.status == 413


def test_storage_corruption_or_missing_file_does_not_silently_return_content(tmp_path):
    store = AttachmentStore(tmp_path / "private")
    metadata = store.put("invoice.png", "image/png", png())
    (store.root / metadata["storage_key"]).write_bytes(b"corrupted")
    with pytest.raises(AccountingError, match="完整性"):
        store.read(metadata)
    store.discard(metadata)
    with pytest.raises(AccountingError, match="不可用"):
        store.read(metadata)


@pytest.mark.parametrize("key", ["../../outside", "/outside", "A" * 32, "aa/../" + "1" * 32])
def test_stored_key_path_traversal_is_rejected(tmp_path, key):
    with pytest.raises(AccountingError):
        AttachmentStore(tmp_path / "private").read({"storage_key": key})
