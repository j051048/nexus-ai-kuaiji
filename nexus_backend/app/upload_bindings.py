"""Server-resolved original references. Never accept a caller supplied binding."""

from .accounting.domain import AccountingError


# purpose: (target kind, target table, original table, original parent column)
BINDINGS = {
    "intake_attachment": ("intake_draft", "intake_drafts", "intake_attachments", "intake_id"),
    "event_attachment": ("business_event", "business_events", "business_attachments", "event_id"),
    "case_attachment": ("case_task", "case_tasks", "case_attachments", "case_id"),
    "material_attachment": (
        "material_request",
        "material_requests",
        "material_attachments",
        "request_id",
    ),
    "bank_import": ("book", "books", "bank_import_files", "book_id"),
    "invoice_import": ("book", "books", "invoice_import_batches", "book_id"),
}


def resolve_target(conn, actor, purpose, target_id):
    if purpose not in BINDINGS or not isinstance(target_id, str) or not target_id:
        raise AccountingError("上传目标无效", 422)
    _kind, table, _attachment, _parent = BINDINGS[purpose]
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id=? AND firm_id=?", (target_id, actor.firm_id)
    ).fetchone()
    if not row:
        raise AccountingError("上传目标不存在或无权访问", 404)
    return dict(row)


def reference(conn, actor, intent, target_id):
    """Resolve an actual original and its immutable parent, including scope and bytes."""
    purpose = intent["purpose"]
    kind, _target_table, table, parent = BINDINGS[purpose]
    target = resolve_target(conn, actor, purpose, target_id)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {parent}=? AND storage_key=? AND firm_id=?",
        (target_id, intent["storage_key"], actor.firm_id),
    ).fetchone()
    if not row:
        raise AccountingError("消费凭证必须绑定已登记的目标原件", 409)
    item = dict(row)
    if item["company_id"] != target["company_id"] or item["created_by"] != actor.user_id:
        raise AccountingError("原件所属企业或上传人不一致", 409)
    if any(item[key] != intent[key] for key in ("storage_key", "sha256", "size_bytes")):
        raise AccountingError("原件与上传凭证的存储身份不一致", 409)
    return {
        "version": 1,
        "firm_id": actor.firm_id,
        "company_id": target["company_id"],
        "purpose": purpose,
        "target_kind": kind,
        "target_id": target_id,
        "attachment_table": table,
        "attachment_id": item["id"],
        "event_id": target_id
        if purpose == "event_attachment"
        else target.get("event_id")
        if purpose == "case_attachment"
        else None,
        "source_id": target.get("source_id") if purpose == "case_attachment" else None,
        "book_id": target_id
        if kind == "book"
        else target.get("book_id")
        if purpose in {"case_attachment", "material_attachment"}
        else None,
        "response_id": item.get("response_id") if purpose == "case_attachment" else None,
        "storage": {key: intent[key] for key in ("storage_key", "sha256", "size_bytes")},
    }
