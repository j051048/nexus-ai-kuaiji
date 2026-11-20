"""Declare an archived text layer for fixtures that later reuse extracted fields.

A blank synthetic image carries no independent text, so the compliance layer
keeps the ``original_unverified`` blocking finding until a human verifies the
original. Cache-positive fixtures therefore declare the text layer an
independent OCR engine would have archived from a digital original, instead of
weakening the control.
"""

from dataclasses import replace


def archived_text(monkeypatch, case):
    """Make document preparation report the provider's own page text as native."""
    from app.intelligence import service

    prepare = service.prepare_document

    def patched(*args, **kwargs):
        pages = prepare(*args, **kwargs)
        document = getattr(case.provider, "document", None) or {}
        rows = document.get("pages") or [{}]
        text = rows[0].get("raw_text", "")
        return [replace(page, native_text=text) for page in pages]

    monkeypatch.setattr(service, "prepare_document", patched)
