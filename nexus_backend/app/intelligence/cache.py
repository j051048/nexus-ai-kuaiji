"""Company-scoped OCR content reuse; no originating event or human decisions escape."""

import json

from ..accounting.service import fingerprint, now
from ..business.service import encode
from .provider import effective_settings
from .ocr import engine_enabled, prompt_version, stored_extraction, source_binding, valid_source

PARSER_VERSION = "document-parser-v1"
CACHE_VERSION = "validated-ocr-cache-v1"


def config_key(settings, media_type, policy_key=None):
    from .structured import is_structured, STRUCTURED_VERSION

    if is_structured(media_type):
        return fingerprint(
            {"parser": STRUCTURED_VERSION, "cache": CACHE_VERSION, "media_type": media_type}
        )
    # Keys are deliberately absent: rotating a secret does not change OCR semantics.
    independent = (
        None
        if not engine_enabled(settings)
        else {
            "url": settings.paddle_url,
            "model": settings.paddle_model,
            "score": settings.paddle_min_score,
            "version": "independent-ocr-v2",
        }
    )
    prompt = prompt_version(settings)
    settings = effective_settings(settings, "ocr")
    return fingerprint(
        {
            "provider": settings.base_url,
            "model": settings.model,
            "format": settings.response_format,
            "prompt": prompt,
            "parser": PARSER_VERSION,
            "cache": CACHE_VERSION,
            "media_type": media_type,
            "independent_ocr": independent,
            "policy": policy_key or "legacy",
        }
    )


def read_cache(db, conn, actor, event, attachment, key):
    if db.is_postgres:
        row = conn.execute(
            "SELECT accounting.read_document_extraction(?,?)", (attachment["id"], key)
        ).fetchone()
        value = row[0] if row else None
    else:
        row = conn.execute(
            "SELECT result_json FROM document_extractions WHERE firm_id=? AND company_id=? AND attachment_sha256=? AND config_key=?",
            (actor.firm_id, event["company_id"], attachment["sha256"], key),
        ).fetchone()
        value = row["result_json"] if row else None
    if not value:
        return None
    try:
        extraction = stored_extraction(json.loads(value))
    except (ValueError, TypeError):
        # Old/unbound or malformed proof is a miss, never silently upgraded to
        # independently verified text. Re-recognize or require a human check.
        return None
    return (
        extraction
        if valid_source(extraction, source_binding(actor, event["company_id"], attachment))
        else None
    )


def store_cache(db, conn, actor, event, attachment, key, run):
    if db.is_postgres:
        conn.execute("SELECT accounting.store_document_extraction(?,?)", (run["id"], key))
    else:
        conn.execute(
            "INSERT INTO document_extractions(firm_id,company_id,attachment_sha256,config_key,result_json,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(firm_id,company_id,attachment_sha256,config_key) DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at",
            (
                actor.firm_id,
                event["company_id"],
                attachment["sha256"],
                key,
                run["result_json"],
                now(),
            ),
        )
