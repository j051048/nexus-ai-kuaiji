"""Current-authority migration tasks, projected before count and pagination.

The enclosing workspace query supplies ``review_books`` and ``edit_books``;
neither a migration task nor its resource ID grants historical access.
"""


def workspace_branch(db, actor):
    reviewer = "EXISTS(SELECT 1 FROM review_books rb WHERE rb.id=p.book_id)"
    period_open = "m.period_status='open'"
    independent = "p.created_by<>? AND ?<>COALESCE((SELECT v.created_by FROM migration_mapping_versions v WHERE v.project_id=p.id ORDER BY v.version DESC LIMIT 1),p.created_by)"
    if db.is_postgres:
        manage = "accounting.migration_manage(p.id)"
        review = "accounting.migration_allowed(p.id,'reviewer') AND " + independent
        read = "accounting.migration_allowed(p.id,'viewer')"
        parameters = [actor.user_id, actor.user_id, actor.firm_id]
    else:
        firm_manager = "EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=p.firm_id AND fm.user_id=? AND fm.role IN ('owner','admin'))"
        ledger_manager = "EXISTS(SELECT 1 FROM ledger_managers lm WHERE lm.ledger_id=p.ledger_id AND lm.user_id=?)"
        manager_grant = "EXISTS(SELECT 1 FROM migration_project_access pa WHERE pa.project_id=p.id AND pa.user_id=? AND pa.role='manager')"
        review_grant = "EXISTS(SELECT 1 FROM migration_project_access pa WHERE pa.project_id=p.id AND pa.user_id=? AND pa.role IN ('manager','reviewer'))"
        any_grant = "EXISTS(SELECT 1 FROM migration_project_access pa WHERE pa.project_id=p.id AND pa.user_id=?)"
        manage = (
            "("
            + reviewer
            + " AND "
            + ledger_manager
            + " AND ("
            + manager_grant
            + " OR "
            + firm_manager
            + "))"
        )
        review = (
            "("
            + reviewer
            + " AND ("
            + review_grant
            + " OR ("
            + firm_manager
            + " AND "
            + ledger_manager
            + ")) AND "
            + independent
            + ")"
        )
        # A physical book grant still requires current firm or company membership.
        book_read = (
            "("
            + firm_manager
            + " OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=p.book_id AND ba.firm_id=p.firm_id AND ba.user_id=? AND (EXISTS(SELECT 1 FROM memberships fm WHERE fm.firm_id=p.firm_id AND fm.user_id=?) OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=p.company_id AND cm.user_id=? AND cm.role IN ('owner','admin','finance')))))"
        )
        read = (
            "("
            + book_read
            + " AND ("
            + any_grant
            + " OR ("
            + firm_manager
            + " AND "
            + ledger_manager
            + " AND "
            + reviewer
            + ")))"
        )
        # SELECT manage(3), review(5), WHERE firm(1), actor active(1), read(7).
        parameters = [actor.user_id] * 8 + [actor.firm_id, actor.user_id] + [actor.user_id] * 7
    active_file = "(j.file_id IS NULL OR EXISTS(SELECT 1 FROM migration_files f WHERE f.id=j.file_id AND f.project_id=p.id AND f.category<>'attachment' AND NOT EXISTS(SELECT 1 FROM migration_file_replacements x WHERE x.old_file_id=f.id)))"
    current_job = "j.project_id=p.id AND j.expected_version=p.version AND " + active_file
    newest = "NOT EXISTS(SELECT 1 FROM migration_jobs n WHERE n.project_id=j.project_id AND n.expected_version=p.version AND n.phase=j.phase AND COALESCE(n.file_id,'')=COALESCE(j.file_id,'') AND (n.created_at,n.id)>(j.created_at,j.id))"
    failed = (
        "EXISTS(SELECT 1 FROM migration_jobs j WHERE "
        + current_job
        + " AND j.status IN ('failed','stale','cancelled') AND "
        + newest
        + ")"
    )
    idle = (
        "NOT EXISTS(SELECT 1 FROM migration_jobs j WHERE "
        + current_job
        + " AND j.status IN ('queued','running','retry_wait'))"
    )
    files = "f.project_id=p.id AND f.category<>'attachment' AND NOT EXISTS(SELECT 1 FROM migration_file_replacements x WHERE x.old_file_id=f.id)"
    parsed = (
        "EXISTS(SELECT 1 FROM migration_files f WHERE "
        + files
        + ") AND NOT EXISTS(SELECT 1 FROM migration_files f WHERE "
        + files
        + " AND NOT EXISTS(SELECT 1 FROM migration_jobs s WHERE s.project_id=p.id AND s.file_id=f.id AND s.phase='parse' AND s.status='succeeded'))"
    )
    no_validation = "NOT EXISTS(SELECT 1 FROM migration_jobs j WHERE j.project_id=p.id AND j.expected_version=p.version AND j.phase='validate' AND j.status='succeeded')"
    next_step = "(" + parsed + " AND " + idle + " AND " + no_validation + ")"
    action = (
        "("
        + period_open
        + " AND ((m.status IN ('collecting','needs_attention') AND m.can_manage=1) OR (m.status='ready' AND m.can_review=1) OR (m.status='accepted' AND m.can_manage=1) OR (m.status='processing' AND m.can_manage=1 AND (m.has_failed_job=1 OR m.parse_complete_idle=1))))"
    )
    sql = (
        """SELECT 'migration' AS kind,m.id AS ref,m.company_id AS company_id,m.book_id AS book_id,
      NULL AS event_id,NULL AS draft_id,NULL AS batch_id,
      '旧账迁移 · ' || CASE m.source_product WHEN 'chanjet_tplus' THEN '畅捷通 T+' WHEN 'yonyou' THEN '用友' WHEN 'kingdee' THEN '金蝶' ELSE '中性账务资料' END || ' · ' || m.history_start || '~' || m.history_end AS title,
      m.status AS state,
      CASE WHEN m.can_manage=1 AND m.period_status='open' AND (m.status='needs_attention' OR (m.status='processing' AND m.has_failed_job=1)) THEN 1 ELSE 2 END AS priority,
      NULL AS due_on,m.updated_at AS updated_at,
      CASE WHEN """
        + action
        + """ THEN 'action' ELSE 'waiting' END AS bucket,
      CASE WHEN m.period_status<>'open' THEN '查看已关期迁移'
        WHEN m.status='collecting' AND m.can_manage=1 THEN '继续准备资料'
        WHEN m.status='needs_attention' AND m.can_manage=1 THEN '处理迁移差异'
        WHEN m.status='ready' AND m.can_review=1 THEN '独立验收'
        WHEN m.status='accepted' AND m.can_manage=1 THEN '确认接管'
        WHEN m.status='processing' AND m.can_manage=1 AND m.has_failed_job=1 THEN '处理迁移任务'
        WHEN m.status='processing' AND m.can_manage=1 AND m.parse_complete_idle=1 THEN '确认映射并核验'
        WHEN m.status='processing' THEN '查看后台进度'
        WHEN m.status='ready' THEN '查看验收进度' ELSE '查看迁移进度' END AS action_label,
      CASE WHEN m.status='processing' AND NOT(m.can_manage=1 AND (m.has_failed_job=1 OR m.parse_complete_idle=1)) THEN '后台迁移任务' WHEN m.status='ready' THEN '独立复核会计' WHEN m.status='accepted' THEN '账套负责人' ELSE '资料经办会计' END AS responsible
      FROM (SELECT p.*,q.status AS period_status,
        CASE WHEN """
        + manage
        + """ THEN 1 ELSE 0 END AS can_manage,
        CASE WHEN """
        + review
        + """ THEN 1 ELSE 0 END AS can_review,
        CASE WHEN """
        + failed
        + """ THEN 1 ELSE 0 END AS has_failed_job,
        CASE WHEN """
        + next_step
        + """ THEN 1 ELSE 0 END AS parse_complete_idle
        FROM migration_projects p JOIN books b ON b.id=p.book_id AND b.firm_id=p.firm_id AND b.company_id=p.company_id
        JOIN periods q ON q.book_id=b.id AND q.period=b.period
        JOIN firm_clients fc ON fc.firm_id=p.firm_id AND fc.company_id=p.company_id AND fc.status='active'
        WHERE p.firm_id=? AND p.status IN ('collecting','processing','needs_attention','ready','accepted')
        """
        + (
            ""
            if db.is_postgres
            else "AND EXISTS(SELECT 1 FROM users u WHERE u.id=? AND u.active=1) "
        )
        + "AND "
        + read
        + ") m"
    )
    return sql, parameters
