"""Shared SQL predicates compiled into both native close guards and API reads.

Reports expose IDs only for handed-off records already within book permission.
Unsubmitted employee material contributes a generic count, never private text.
"""


def _json(dialect):
    def value(column, path):
        parts = path.split(".")
        return (
            f"json_extract(CASE WHEN json_valid({column}) THEN {column} ELSE 'null' END,'$.{path}')"
            if dialect == "sqlite"
            else f"jsonb_extract_path_text(({column})::jsonb,"
            + ",".join(f"'{part}'" for part in parts)
            + ")"
        )

    def array(column, path, alias):
        if dialect == "sqlite":
            safe = f"CASE WHEN json_valid({column}) THEN {column} ELSE 'null' END"
            return f"json_each(CASE WHEN json_type({safe},'$.{path}')='array' THEN json_extract({safe},'$.{path}') ELSE '[]' END) {alias}"
        extracted = (
            f"jsonb_extract_path(({column})::jsonb,"
            + ",".join(f"'{part}'" for part in path.split("."))
            + ")"
        )
        return f"jsonb_array_elements(CASE WHEN jsonb_typeof({extracted})='array' THEN {extracted} ELSE '[]'::jsonb END) {alias}(value)"

    return value, array


def date_valid_sql(dialect, value):
    if dialect == "postgres":
        return f"accounting.closing_date_valid({value})"
    return f"COALESCE(length({value})=10 AND {value} GLOB '20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]' AND strftime('%Y-%m-%d',{value},'+0 days')={value},0)=1"


def receipt_valid_sql(dialect, export_alias="x"):
    j, each = _json(dialect)
    equal = lambda left, right: (
        f"({left}) IS NOT DISTINCT FROM ({right})"
        if dialect == "postgres"
        else f"({left}) IS ({right})"
    )
    env = " AND ".join(
        f"{j('r.body_json', key)}={j('m.payload_json', key)}"
        for key in ("book_id", "company_id", "period", "currency")
    )
    fields = " AND ".join(
        equal(j("i.value", key), j("expected.value", key))
        for key in ("date", "amount", "description")
    )
    line_fields = " AND ".join(
        equal(j("line.value", key), j("wanted.value", key))
        for key in ("ordinal", "account_code", "debit", "credit")
    )
    aux_fields = " AND ".join(
        equal(
            j("line.value", "auxiliary_values." + key), j("wanted.value", "auxiliary_values." + key)
        )
        for key in ("department", "employee", "counterparty", "project")
    )
    if dialect == "sqlite":

        def object_count(column):
            safe = f"CASE WHEN json_valid({column}) THEN {column} ELSE 'null' END"
            return f"(SELECT count(*) FROM json_each(CASE WHEN json_type({safe},'$.auxiliary_values')='object' THEN json_extract({safe},'$.auxiliary_values') ELSE '{{}}' END))"

        aux_count = object_count("line.value") + "=" + object_count("wanted.value")
    else:
        aux_count = "line.value->'auxiliary_values'=wanted.value->'auxiliary_values'"
    # The presence of an object is mandatory; equal NULLs alone cannot prove
    # that the downstream software received the auxiliary dimensions.
    aux_object = (
        "json_type(CASE WHEN json_valid(line.value) THEN line.value ELSE 'null' END,'$.auxiliary_values')='object'"
        if dialect == "sqlite"
        else "jsonb_typeof(line.value->'auxiliary_values')='object'"
    )
    return f"""EXISTS(SELECT 1 FROM delivery_manifests m JOIN delivery_receipts r ON r.export_id=m.export_id AND r.book_id=m.book_id
      WHERE m.export_id={export_alias}.id AND m.book_id={export_alias}.book_id AND {env}
      AND NOT EXISTS(SELECT 1 FROM delivery_receipts newer WHERE newer.export_id=r.export_id AND newer.book_id=r.book_id AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id))
      AND (SELECT count(*) FROM {each("r.body_json", "items", "i")})=(SELECT count(*) FROM {each("m.payload_json", "items", "expected")})
      AND (SELECT count(*) FROM {each("r.body_json", "items", "i")})={export_alias}.count
      AND NOT EXISTS(SELECT 1 FROM {each("r.body_json", "items", "i")}
        WHERE {j("i.value", "status")} IS NULL OR {j("i.value", "status")}<>'accepted' OR trim(COALESCE({j("i.value", "external_voucher_id")},''))=''
        OR (SELECT count(*) FROM {each("r.body_json", "items", "duplicate")} WHERE {j("duplicate.value", "draft_id")}={j("i.value", "draft_id")})<>1
        OR (SELECT count(*) FROM {each("r.body_json", "items", "duplicate")} WHERE {j("duplicate.value", "external_voucher_id")}={j("i.value", "external_voucher_id")})<>1
        OR EXISTS(SELECT 1 FROM delivery_receipt_items h WHERE h.book_id=r.book_id AND h.receipt_id<>r.id AND
          (h.claimed_draft_id<>{j("i.value", "draft_id")} AND h.external_voucher_id={j("i.value", "external_voucher_id")}
           OR h.claimed_draft_id={j("i.value", "draft_id")} AND trim(COALESCE(h.external_voucher_id,''))<>'' AND h.external_voucher_id<>{j("i.value", "external_voucher_id")}))
        OR NOT EXISTS(SELECT 1 FROM {each("m.payload_json", "items", "expected")} WHERE {j("expected.value", "draft_id")}={j("i.value", "draft_id")} AND {fields}
          AND (SELECT count(*) FROM {each("i.value", "lines", "line")})=(SELECT count(*) FROM {each("expected.value", "lines", "wanted")})
          AND (SELECT count(*) FROM {each("i.value", "lines", "line")})>=2
          AND NOT EXISTS(SELECT 1 FROM {each("i.value", "lines", "line")} WHERE NOT EXISTS(SELECT 1 FROM {each("expected.value", "lines", "wanted")}
            WHERE {line_fields} AND {aux_object} AND {aux_fields} AND {aux_count})
            OR (SELECT count(*) FROM {each("i.value", "lines", "duplicate_line")} WHERE {j("duplicate_line.value", "ordinal")}={j("line.value", "ordinal")})<>1)))
    )"""


# Every mutable fact affecting review, generation, reconciliation and delivery is
# represented in the private basis. Only its digest is ever sent to the browser.
FACT_SCOPES = {
    "books": "t.id=b.id",
    "periods": "t.book_id=b.id",
    "sources": "t.book_id=b.id",
    "drafts": "t.book_id=b.id",
    "draft_lines": "t.book_id=b.id",
    "draft_line_auxiliaries": "t.book_id=b.id",
    "accounts": "t.book_id=b.id",
    "source_evidence": "t.book_id=b.id",
    "business_mappings": "t.book_id=b.id",
    "business_mapping_versions": "t.book_id=b.id",
    "business_events": "t.firm_id=b.firm_id AND t.company_id=b.company_id AND (substr({date},1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions m WHERE m.event_id=t.id AND m.book_id=b.id))",
    "business_attachments": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "case_tasks": "t.book_id=b.id OR t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "case_responses": "t.case_id IN(SELECT c.id FROM case_tasks c WHERE c.book_id=b.id OR c.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id))",
    "case_supersessions": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "ocr_jobs": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "proposal_jobs": "t.book_id=b.id",
    "ai_runs": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "model_costs": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "ai_findings": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "ai_finding_reviews": "t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id)",
    "ai_bank_links": "t.book_id=b.id",
    "bank_import_keys": "t.book_id=b.id",
    "bank_duplicate_decisions": "t.book_id=b.id",
    "bank_source_dispositions": "t.book_id=b.id",
    "exports": "t.book_id=b.id",
    "delivery_manifests": "t.book_id=b.id",
    "delivery_receipts": "t.book_id=b.id",
    "delivery_receipt_items": "t.book_id=b.id",
    "fact_revisions": "t.old_book_id=b.id OR t.target_book_id=b.id",
    "fact_revision_lineage": "t.old_book_id=b.id OR t.new_book_id=b.id",
    "company_master_entities": "t.firm_id=b.firm_id AND t.company_id=b.company_id",
    "company_master_aliases": "t.firm_id=b.firm_id AND t.company_id=b.company_id",
    "journal_templates": "t.book_id=b.id",
    "journal_template_versions": "t.book_id=b.id",
    "learning_rules": "t.book_id=b.id",
    "settlement_items": "t.book_id=b.id",
    "settlement_allocations": "t.book_id=b.id",
    "settlement_reversals": "t.book_id=b.id",
}


def fact_scope_sql(dialect, scope):
    j, _ = _json(dialect)
    date_t = j("t.payload", "occurred_on")
    scope = scope.replace("{date}", date_t)
    if "substr(" + date_t + ",1,7)=b.period" in scope:
        scope = scope.replace(
            "substr(" + date_t + ",1,7)=b.period",
            "substr("
            + date_t
            + ",1,7)=b.period OR (t.status<>'handed_off' AND NOT ("
            + date_valid_sql(dialect, date_t)
            + "))",
        )
    raw = "e.firm_id=b.firm_id AND e.company_id=b.company_id"
    date_e = j("e.payload", "occurred_on")
    scoped = (
        raw
        + " AND (substr("
        + date_e
        + ",1,7)=b.period OR (e.status<>'handed_off' AND NOT ("
        + date_valid_sql(dialect, date_e)
        + ")) OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id))"
    )
    return scope.replace(raw, scoped)


def issues_sql(dialect, target="?"):
    j, _ = _json(dialect)
    inactive = "NOT EXISTS(SELECT 1 FROM bank_source_dispositions z WHERE z.book_id=s.book_id AND z.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM fact_revision_lineage z WHERE z.old_book_id=s.book_id AND z.old_source_id=s.id)"
    return f"""WITH book AS (SELECT * FROM books WHERE id={target}),
      active_sources AS (SELECT s.* FROM sources s JOIN book b ON b.id=s.book_id WHERE {inactive}),
      active_drafts AS (SELECT d.* FROM drafts d JOIN active_sources s ON s.id=d.source_id AND s.book_id=d.book_id
        WHERE NOT EXISTS(SELECT 1 FROM ai_bank_links link WHERE link.bank_source_id=s.id AND link.book_id=s.book_id)),
      event_scope AS (SELECT e.*,b.id AS scope_book_id FROM business_events e JOIN book b ON b.firm_id=e.firm_id AND b.company_id=e.company_id
        WHERE substr({j("e.payload", "occurred_on")},1,7)=b.period OR EXISTS(SELECT 1 FROM business_mappings m WHERE m.event_id=e.id AND m.book_id=b.id)),
      issues AS (
      SELECT 'unreviewed' AS category,'draft' AS object_type,d.id AS object_id,'尚有未审核或未导出的有效凭证' AS detail FROM active_drafts d WHERE d.status<>'exported'
      UNION ALL SELECT 'unprocessed','source',s.id,'来源尚未形成有效凭证草稿' FROM active_sources s WHERE NOT EXISTS(SELECT 1 FROM drafts d WHERE d.book_id=s.book_id AND d.source_id=s.id)
      UNION ALL SELECT 'missing_evidence','source',s.id,'当前有效来源缺少原件或证据引用' FROM active_sources s WHERE trim(COALESCE((SELECT se.evidence_ref FROM source_evidence se WHERE se.book_id=s.book_id AND se.source_id=s.id ORDER BY se.id DESC LIMIT 1),s.evidence_ref))=''
      UNION ALL SELECT 'business_pending','book',b.id,'本企业本期仍有未完成审批或移交的业务事项，请企业财务核对' FROM book b WHERE EXISTS(SELECT 1 FROM event_scope e WHERE e.scope_book_id=b.id AND e.status<>'handed_off')
      UNION ALL SELECT 'missing_evidence','book',b.id,'本企业仍有发生日期缺失或无效的未移交资料，无法确定归期，请企业财务修正' FROM book b WHERE EXISTS(SELECT 1 FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND e.status<>'handed_off' AND NOT ({date_valid_sql(dialect, j("e.payload", "occurred_on"))}))
      UNION ALL SELECT 'missing_evidence','event',m.event_id,'已移交事项缺少当前有效附件' FROM business_mappings m JOIN book b ON b.id=m.book_id
        WHERE NOT EXISTS(SELECT 1 FROM business_attachments a WHERE a.event_id=m.event_id AND NOT EXISTS(SELECT 1 FROM case_supersessions z WHERE z.event_id=a.event_id AND z.old_attachment_id=a.id))
      UNION ALL SELECT 'open_cases','case',c.id,'本账套仍有未关闭的异常或补件待办' FROM case_tasks c JOIN book b ON c.book_id=b.id OR c.event_id IN(SELECT m.event_id FROM business_mappings m WHERE m.book_id=b.id) WHERE c.state<>'closed'
      UNION ALL SELECT 'open_cases','book',b.id,'本企业本期未移交事项仍有待处理补件，请企业财务核对' FROM book b WHERE EXISTS(SELECT 1 FROM case_tasks c JOIN event_scope e ON e.id=c.event_id AND e.scope_book_id=b.id WHERE c.book_id IS NULL AND c.state<>'closed' AND NOT EXISTS(SELECT 1 FROM business_mappings m WHERE m.event_id=e.id AND m.book_id=b.id))
      UNION ALL SELECT 'background_pending','ocr_job',q.id,'票据识别仍在运行或存在发送结果未知的任务' FROM ocr_jobs q JOIN event_scope e ON e.id=q.event_id WHERE q.status IN ('queued','running','retry_wait') AND NOT EXISTS(SELECT 1 FROM case_supersessions z WHERE z.event_id=q.event_id AND z.old_attachment_id=q.attachment_id) AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'background_pending','proposal_job',q.id,'分录任务仍在运行或等待人工处理' FROM proposal_jobs q JOIN book b ON b.id=q.book_id WHERE q.status IN ('waiting','queued','running','retry_wait') OR q.status IN ('needs_attention','interrupted','failed') AND EXISTS(SELECT 1 FROM drafts d WHERE d.id=q.draft_id AND d.book_id=q.book_id AND d.status='draft')
      UNION ALL SELECT 'background_pending','event',r.event_id,'仍有未完成的模型执行记录' FROM ai_runs r JOIN event_scope e ON e.id=r.event_id WHERE r.status='running' AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'model_uncertain','event',c.event_id,'仍有模型外发结果或费用未知的记录' FROM model_costs c JOIN event_scope e ON e.id=c.event_id WHERE c.state='uncertain' AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'unmatched_bank','source',s.id,'银行来源未匹配业务事项，也未形成独立已导出的凭证' FROM active_sources s WHERE s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.bank_source_id=s.id AND l.book_id=s.book_id) AND NOT EXISTS(SELECT 1 FROM drafts d WHERE d.source_id=s.id AND d.book_id=s.book_id AND d.status='exported')
      UNION ALL SELECT 'unmatched_bank','source',s.id,'银行收付核销尚未分配完整金额，待清偿事项仍须核对' FROM active_sources s WHERE s.source_kind='bank'
        AND EXISTS(SELECT 1 FROM settlement_allocations a WHERE a.book_id=s.book_id AND a.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id AND r.book_id=a.book_id))
        AND (SELECT COALESCE(sum(a.amount_cents),0) FROM settlement_allocations a WHERE a.book_id=s.book_id AND a.bank_source_id=s.id AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id AND r.book_id=a.book_id))<>s.amount_cents
      UNION ALL SELECT 'unmatched_business','event',m.event_id,'已支付业务事项尚未匹配银行来源，请先核对防止重复记账' FROM business_mappings m JOIN event_scope e ON e.id=m.event_id AND e.scope_book_id=m.book_id JOIN book b ON b.id=m.book_id
        WHERE {j("e.payload", "payment_status")}='paid' AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.event_id=m.event_id AND l.book_id=m.book_id)
        AND NOT EXISTS(SELECT 1 FROM fact_revision_lineage z WHERE z.old_book_id=m.book_id AND z.old_source_id=m.source_id)
      UNION ALL SELECT 'delivery_pending','export',x.id,'导出后缺少内容一致的完整导入回执，或回执仍有未知及差异' FROM exports x JOIN book b ON b.id=x.book_id WHERE NOT {receipt_valid_sql(dialect)}
      UNION ALL SELECT 'fact_revision','revision',r.id,'业务事实修订或已导出调整建议尚未完成独立处置' FROM fact_revisions r JOIN book b ON r.old_book_id=b.id OR r.target_book_id=b.id WHERE r.state IN ('pending','adjustment_proposed')
      ) SELECT issues.category,issues.object_type,issues.object_id,issues.detail FROM issues ORDER BY issues.category,issues.object_type,issues.object_id"""


def sqlite_issue_view_sql():
    sql = issues_sql("sqlite", "b_all.id").replace(
        "WITH book AS (SELECT * FROM books WHERE id=b_all.id)", "WITH book AS (SELECT * FROM books)"
    )
    sql = sql.replace(
        "SELECT 'unreviewed' AS category", "SELECT d.book_id AS book_id,'unreviewed' AS category"
    )
    branches = [
        ("unprocessed", "s.book_id"),
        ("missing_evidence", "s.book_id"),
        ("business_pending", "b.id"),
        ("missing_evidence", "b.id"),
        ("missing_evidence", "m.book_id"),
        ("open_cases", "b.id"),
        ("open_cases", "b.id"),
        ("background_pending", "m_book.book_id"),
        ("background_pending", "q.book_id"),
        ("background_pending", "m_book.book_id"),
        ("model_uncertain", "m_book.book_id"),
        ("unmatched_bank", "s.book_id"),
        ("unmatched_bank", "s.book_id"),
        ("unmatched_business", "m.book_id"),
        ("delivery_pending", "x.book_id"),
        ("fact_revision", "b.id"),
    ]
    for category, scope in branches:
        sql = sql.replace(
            "UNION ALL SELECT '" + category + "'",
            "UNION ALL SELECT " + scope + ",'" + category + "'",
            1,
        )
    for alias, suffix in [("q", "q.status"), ("r", "r.status"), ("c", "c.state")]:
        sql = sql.replace(
            f"JOIN event_scope e ON e.id={alias}.event_id WHERE {suffix}",
            f"JOIN event_scope e ON e.id={alias}.event_id JOIN business_mappings m_book ON m_book.event_id=e.id AND m_book.book_id=e.scope_book_id WHERE {suffix}",
        )
    return (
        "CREATE VIEW closing_issue_rows AS "
        + sql.replace(
            "SELECT issues.category,issues.object_type,issues.object_id,issues.detail FROM issues",
            "SELECT issues.book_id,issues.category,issues.object_type,issues.object_id,issues.detail FROM issues",
        )
        + ";\n"
    )
