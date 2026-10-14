"""Shared deterministic receipt/fact predicates for monthly and closing guards."""

from ..closing.queries import receipt_valid_sql


def current_inputs_sql():
    verified = receipt_valid_sql("postgres")
    return f"""WITH verified_exports AS (SELECT x.* FROM accounting.exports x WHERE x.book_id=target_book AND {verified}),
    verified_drafts AS(SELECT value->>'draft_id' AS draft_id FROM verified_exports x JOIN accounting.delivery_manifests m ON m.export_id=x.id CROSS JOIN jsonb_array_elements(m.payload_json::jsonb->'items')),
    facts AS(SELECT d.id,d.version,d.status,l.ordinal,l.account_code,l.debit_cents,l.credit_cents,coalesce(a.values_json::jsonb,'{{}}') AS aux,
      CASE WHEN d.status='draft' THEN 'unreviewed' WHEN d.status='approved' THEN 'approved_pending_delivery' WHEN EXISTS(SELECT 1 FROM verified_drafts vd WHERE vd.draft_id=d.id) THEN 'verified_delivery' ELSE 'exported_pending_receipt' END AS grp
      FROM accounting.drafts d JOIN accounting.sources s ON s.id=d.source_id AND s.book_id=d.book_id JOIN accounting.draft_lines l ON l.draft_id=d.id AND l.book_id=d.book_id LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
      WHERE d.book_id=target_book AND NOT accounting.source_inactive(s.book_id,s.id) AND NOT EXISTS(SELECT 1 FROM accounting.ai_bank_links link WHERE link.bank_source_id=s.id AND link.book_id=s.book_id)),
    receipts AS(SELECT r.* FROM accounting.delivery_receipts r WHERE r.book_id=target_book AND r.id=(SELECT r2.id FROM accounting.delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1))
    SELECT jsonb_build_object('book_id',b.id,'period',b.period,'opening_id',target_opening,'closing_id',target_closing,
      'facts',coalesce((SELECT jsonb_agg(jsonb_build_object('draft_id',id,'version',version,'status',status,'ordinal',ordinal,'account_code',account_code,'debit_cents',debit_cents,'credit_cents',credit_cents,'auxiliary_values',aux,'group',grp) ORDER BY id,ordinal) FROM facts),'[]'),
      'receipts',coalesce((SELECT jsonb_agg(jsonb_build_object('receipt_id',id,'export_id',export_id,'body_hash',request_fingerprint) ORDER BY created_at,id) FROM receipts),'[]')) FROM accounting.books b WHERE b.id=target_book"""


MONTHLY_FACT_SCOPES = {
    "monthly_baselines": "t.book_id=b.id",
    "monthly_baseline_confirmations": "t.book_id=b.id",
    "monthly_checks": "t.book_id=b.id",
    "monthly_check_closures": "t.book_id=b.id",
    "recurring_plan_versions": "t.ledger_id IN(SELECT ledger_id FROM ledger_periods WHERE book_id=b.id)",
    "recurring_plan_approvals": "t.ledger_id IN(SELECT ledger_id FROM ledger_periods WHERE book_id=b.id)",
    "recurring_generations": "t.book_id=b.id",
}


def monthly_issue_sql(dialect):
    current = (
        "c.inputs_json::jsonb=accounting.monthly_current_inputs(c.book_id,c.opening_id,c.closing_id)"
        if dialect == "postgres"
        else "EXISTS(SELECT 1 FROM monthly_check_currentness n WHERE n.check_id=c.id AND n.is_current=1)"
    )
    return f"""SELECT c.book_id,'monthly_reference' AS category,'monthly_check' AS object_type,c.id AS object_id,'月度外部余额检查尚未人工核对，或依据已变化，须重新检查' AS detail FROM monthly_checks c
    WHERE c.version=(SELECT max(version) FROM monthly_checks WHERE book_id=c.book_id) AND (NOT EXISTS(SELECT 1 FROM monthly_check_closures x WHERE x.check_id=c.id) OR NOT({current}) OR EXISTS(SELECT 1 FROM monthly_baselines o WHERE o.book_id=c.book_id AND o.kind='opening' AND o.version>(SELECT version FROM monthly_baselines WHERE id=c.opening_id)) OR EXISTS(SELECT 1 FROM monthly_baselines z WHERE z.book_id=c.book_id AND z.kind='closing' AND z.version>(SELECT version FROM monthly_baselines WHERE id=c.closing_id)))
    UNION ALL SELECT m.book_id,'monthly_reference','monthly_baseline',m.id,'月度外部余额尚未独立确认或未形成对应检查' FROM monthly_baselines m
    WHERE m.version=(SELECT max(version) FROM monthly_baselines WHERE book_id=m.book_id AND kind=m.kind) AND (NOT EXISTS(SELECT 1 FROM monthly_baseline_confirmations x WHERE x.baseline_id=m.id) OR NOT EXISTS(SELECT 1 FROM monthly_checks c WHERE c.book_id=m.book_id AND (c.opening_id=m.id OR c.closing_id=m.id)))
    UNION ALL SELECT b.id,'recurring_pending','recurring_plan',p.plan_id,'本月有已审批的固定事项计划尚未生成草稿，请核对实际发生情况' FROM recurring_plan_versions p JOIN recurring_plan_approvals a ON a.plan_id=p.plan_id AND a.version=p.version JOIN ledger_periods l ON l.ledger_id=p.ledger_id JOIN books b ON b.id=l.book_id
    WHERE p.version=(SELECT max(x.version) FROM recurring_plan_versions x JOIN recurring_plan_approvals reviewed ON reviewed.plan_id=x.plan_id AND reviewed.version=x.version WHERE x.plan_id=p.plan_id) AND b.period>=p.start_period AND (p.end_period IS NULL OR b.period<=p.end_period) AND NOT EXISTS(SELECT 1 FROM recurring_generations g WHERE g.plan_id=p.plan_id AND g.book_id=b.id)"""
