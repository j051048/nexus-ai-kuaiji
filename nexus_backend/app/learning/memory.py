"""Read-only, current-source projection of existing enterprise rule candidates."""

from app.accounting.domain import AccountingError
from app.accounting.review_policy import ReviewPolicyService
from app.accounting.service import fingerprint
from .evidence import reviewed_source
from .models import RuleDefinition


def snapshot(service, conn, actor, book_id, rule_id, version, state_version):
    book = service._book(conn, actor, book_id)
    row = service._rule(conn, actor, book_id, rule_id)
    if row["current_version"] != version or row["state_version"] != state_version:
        raise AccountingError("规则版本已变化，请重新打开当前规则", 409)
    rule = service._rule_json(row)
    sources, unavailable = [], []
    # No recall from another period and no unbounded history scan.
    if not 1 <= len(rule["sample_ids"]) <= 100:
        unavailable.append("样例数量不在当前可核对范围")
    else:
        for sample_id in rule["sample_ids"]:
            sample = service.sample_in_transaction(conn, actor, book_id, sample_id)
            try:
                bound = reviewed_source(service, conn, actor, book, sample)
            except AccountingError:
                unavailable.append("样例当前复核或来源已失效，请重新核对")
                continue
            sources.append(
                {
                    "sample_id": sample_id,
                    "draft_id": sample["draft_id"],
                    "reviewed_version": sample["reviewed_version"],
                    "reviewed_by": sample["reviewed_by"],
                    "source_fingerprint": fingerprint(bound),
                    "original_hashes": sorted({a["sha256"] for a in bound["attachments"]}),
                }
            )
    identity = service.resolve_counterparty_in_transaction(
        conn, actor, book_id, rule["definition"]["conditions"]["counterparty"]
    )
    requested = rule["definition"]["conditions"].get("counterparty_id")
    if identity["status"] != "resolved" or (requested and requested != identity["entity_id"]):
        unavailable.append("往来方身份不明确或已变化，请先核对企业主数据")
    try:
        service.catalog._validate_template(
            conn, book_id, RuleDefinition(**rule["definition"]).as_template()
        )
    except AccountingError:
        unavailable.append("科目或辅助核算要求已变化，请重新核对模板")
    catalog = [
        dict(r)
        for r in conn.execute(
            "SELECT code,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
            (book_id,),
        )
    ]
    state = (
        "retired"
        if rule["status"] == "retired"
        else "blocked"
        if unavailable
        else "active"
        if rule["status"] == "published"
        else "candidate"
    )
    return {
        "schema": "enterprise-rule-memory-v1",
        "scope": {
            "firm_id": actor.firm_id,
            "company_id": book["company_id"],
            "book_id": book_id,
            "period": book["period"],
        },
        "rule_id": rule_id,
        "version": version,
        "state_version": state_version,
        "state": state,
        "sources": sources,
        "unavailable": sorted(set(unavailable)),
        "policy_fingerprint": fingerprint(ReviewPolicyService.policy_in_transaction(conn, book_id)),
        "master_fingerprint": service._master_fingerprint(conn, actor, book_id),
        "catalog_fingerprint": fingerprint(catalog),
        "definition_fingerprint": rule["semantic_fingerprint"],
        "auto_apply": False,
        "boundary": "仅本企业、本账套、本月份的处理模板；历史金额不作为当前事实，评测不等于发布或复核。",
        "next_action": (
            "已停用，仅保留追溯"
            if state == "retired"
            else "先处理失效依据，再重新评测"
            if state == "blocked"
            else "使用当前事项事实，并重新走复核流程"
            if state == "active"
            else "选择独立反例进行评测；是否发布仍需人工明确确认"
        ),
    }


def project(service, actor, book_id, rule_id, *, expected_version, expected_state_version):
    args = (actor, book_id, rule_id, expected_version, expected_state_version)
    with service.db.transaction(actor=actor) as conn:
        value = snapshot(service, conn, *args)
    # Repeatable snapshots cannot see revocation/retirement committed after read.
    with service.db.transaction(actor=actor) as conn:
        current = snapshot(service, conn, *args)
        if fingerprint(current) != fingerprint(value):
            raise AccountingError("规则依据或权限已变化，请重新读取", 409)
    return {**value, "fingerprint": fingerprint(value)}
