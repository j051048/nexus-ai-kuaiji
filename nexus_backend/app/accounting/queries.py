"""Bounded workbench reads. Summaries never load invoice text or all journal lines.

The SQL health flag is a basic accounting summary; approval/export always execute
the full deterministic and intelligence checks against current evidence.
"""
from .domain import AccountingError


def inactive_source_sql(dialect='sqlite', alias='s'):
    if alias not in {'s', 'peer', 'o'}:
        raise ValueError('Unknown source alias')
    if dialect == 'postgres':
        return f"accounting.source_inactive({alias}.book_id,{alias}.id)"
    return f"(EXISTS(SELECT 1 FROM fact_revision_lineage rl WHERE rl.old_book_id={alias}.book_id AND rl.old_source_id={alias}.id) OR EXISTS(SELECT 1 FROM bank_source_dispositions sd WHERE sd.book_id={alias}.book_id AND sd.source_id={alias}.id))"


def page_bounds(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise AccountingError("分页须为1–100条，偏移不得超过1000000")


def health_cte(scope_sql, dialect='sqlite'):
    revision_blocked = ("accounting.revision_source_blocked(s.book_id,s.id)" if dialect == 'postgres' else
                        "EXISTS(SELECT 1 FROM fact_revisions fr WHERE fr.old_book_id=s.book_id AND fr.old_source_id=s.id AND fr.state='pending')")
    auxiliary_missing = []
    for key in ('department', 'employee', 'counterparty', 'project'):
        if dialect == 'postgres':
            required = f"jsonb_exists(CAST(ac.auxiliary_requirements AS jsonb),'{key}')"
            value = f"jsonb_extract_path_text(CAST(ax.values_json AS jsonb),'{key}')"
        else:
            required = f"EXISTS(SELECT 1 FROM json_each(ac.auxiliary_requirements) r WHERE r.value='{key}')"
            value = f"json_extract(ax.values_json,'$.{key}')"
        auxiliary_missing.append(f"({required} AND trim(COALESCE({value},''))='')")
    auxiliary_missing = ' OR '.join(auxiliary_missing)
    return f"""WITH visible_books AS ({scope_sql}),
    totals AS (
      SELECT l.draft_id,l.book_id,COUNT(*) AS line_count,SUM(l.debit_cents) AS debit,
        SUM(l.credit_cents) AS credit,
        SUM(CASE WHEN substr(l.account_code,1,4)='1002' THEN l.debit_cents ELSE 0 END) AS bank_debit,
        SUM(CASE WHEN substr(l.account_code,1,4)='1002' THEN l.credit_cents ELSE 0 END) AS bank_credit,
        SUM(CASE WHEN ac.active=1 AND (l.debit_cents>0 AND l.credit_cents=0 OR l.credit_cents>0 AND l.debit_cents=0) THEN 0 ELSE 1 END) AS invalid_lines
        ,SUM(CASE WHEN ac.auxiliary_requirements='[]' THEN 0 WHEN {auxiliary_missing} THEN 1 ELSE 0 END) AS missing_auxiliaries
      FROM draft_lines l JOIN visible_books v ON v.id=l.book_id
      LEFT JOIN accounts ac ON ac.book_id=l.book_id AND ac.code=l.account_code
      LEFT JOIN draft_line_auxiliaries ax ON ax.draft_id=l.draft_id AND ax.book_id=l.book_id AND ax.ordinal=l.ordinal
      GROUP BY l.draft_id,l.book_id
    ), health AS (
      SELECT d.*,s.date AS source_date,s.amount_cents,s.direction,s.counterparty,
        CASE WHEN {inactive_source_sql(dialect)} THEN 1 ELSE 0 END AS source_inactive,
        s.source_kind,s.description AS source_description,s.external_id,
        COALESCE((SELECT e.evidence_ref FROM source_evidence e WHERE e.source_id=s.id AND e.book_id=s.book_id ORDER BY e.id DESC LIMIT 1),s.evidence_ref) AS evidence_ref,
        CASE WHEN trim(COALESCE((SELECT e.evidence_ref FROM source_evidence e WHERE e.source_id=s.id AND e.book_id=s.book_id ORDER BY e.id DESC LIMIT 1),s.evidence_ref))=''
          OR COALESCE(t.line_count,0)<2 OR COALESCE(t.debit,0)<>s.amount_cents
          OR COALESCE(t.credit,0)<>s.amount_cents OR COALESCE(t.invalid_lines,0)>0
          OR COALESCE(t.missing_auxiliaries,0)>0
          OR {revision_blocked}
          OR (s.source_kind='bank' AND (COALESCE(t.bank_debit,0)<>CASE WHEN s.direction='income' THEN s.amount_cents ELSE 0 END
            OR COALESCE(t.bank_credit,0)<>CASE WHEN s.direction='expense' THEN s.amount_cents ELSE 0 END))
          OR EXISTS(SELECT 1 FROM ai_bank_links bl WHERE bl.bank_source_id=s.id AND bl.book_id=s.book_id)
          OR CASE WHEN EXISTS(SELECT 1 FROM bank_import_keys known_bank WHERE known_bank.book_id=s.book_id)
            THEN CASE WHEN EXISTS(SELECT 1 FROM bank_import_keys marker JOIN sources peer ON peer.book_id=marker.book_id AND peer.id<>s.id
            LEFT JOIN bank_import_keys own_identity ON own_identity.source_id=s.id AND own_identity.book_id=s.book_id
            LEFT JOIN bank_import_keys peer_identity ON peer_identity.source_id=peer.id AND peer_identity.book_id=peer.book_id
            WHERE marker.book_id=s.book_id AND (marker.source_id=s.id OR marker.source_id=peer.id)
              AND s.source_kind='bank' AND peer.source_kind='bank'
              AND peer.date=s.date AND peer.amount_cents=s.amount_cents AND peer.direction=s.direction AND peer.counterparty=s.counterparty
              AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions iso WHERE iso.book_id=peer.book_id AND iso.source_id=peer.id)
              AND (own_identity.source_id IS NULL OR peer_identity.source_id IS NULL OR own_identity.bank_account=peer_identity.bank_account)
              AND NOT EXISTS(SELECT 1 FROM bank_duplicate_decisions dc WHERE dc.book_id=s.book_id
                AND dc.source_id=CASE WHEN s.id<peer.id THEN s.id ELSE peer.id END
                AND dc.other_source_id=CASE WHEN s.id<peer.id THEN peer.id ELSE s.id END)) THEN 1 ELSE 0 END ELSE 0 END=1
          OR EXISTS(SELECT 1 FROM case_tasks ct WHERE ct.state<>'closed' AND
            (ct.source_id=s.id AND ct.book_id=s.book_id OR ct.draft_id=d.id AND ct.book_id=d.book_id
             OR ct.event_id IN (SELECT bm.event_id FROM business_mappings bm WHERE bm.source_id=s.id AND bm.book_id=s.book_id)))
          THEN 1 ELSE 0 END AS basic_exception
      FROM drafts d JOIN visible_books v ON v.id=d.book_id
      JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id
      LEFT JOIN totals t ON t.draft_id=d.id AND t.book_id=d.book_id
    ), summaries AS (
      SELECT v.*,COALESCE(SUM(CASE WHEN h.status='draft' AND h.source_inactive=0 THEN 1 ELSE 0 END),0) AS pending_count,
        COALESCE(SUM(CASE WHEN h.status IN ('approved','exported') THEN 1 ELSE 0 END),0) AS approved_count,
        COALESCE(SUM(CASE WHEN h.status='draft' AND h.source_inactive=0 THEN h.basic_exception ELSE 0 END),0) AS exception_count
      FROM visible_books v LEFT JOIN health h ON h.book_id=v.id
      GROUP BY v.id,v.name,v.period,v.company_id,v.company_name,v.period_status,v.book_role,v.company_role
    ) """


def book_filter(name, search):
    if name not in {'all', 'pending', 'exception', 'approved'} or not isinstance(search, str) or len(search)>120:
        raise AccountingError("待办筛选参数无效")
    condition = {'all': '1=1', 'pending': 'pending_count>0', 'exception': 'exception_count>0', 'approved': 'approved_count>0'}[name]
    escaped = search.strip().lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return condition + " AND (LOWER(name) LIKE ? ESCAPE '\\' OR LOWER(company_name) LIKE ? ESCAPE '\\')", ['%'+escaped+'%', '%'+escaped+'%']
