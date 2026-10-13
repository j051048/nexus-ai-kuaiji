from uuid import uuid4
import pytest
from app.accounting.models import ApprovalRequest,DraftUpdate
from app.accounting.service import fingerprint,now
from app.learning.models import RuleCreate,RuleDefinition
from app.learning.service import LearningService,encode
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage10_settlements_postgres import prepare,preview,confirm

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason="需要专用 PG 管理员及受限 API 测试连接")


def cleared(c):
    service,original,item,bank=prepare(c)
    confirm(c,service,preview(c,service,item,bank))
    d=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    c.ledger.approve(c.actors["accountant"],c.book["id"],d["id"],ApprovalRequest(expected_version=d["version"]))
    value=service.clearance(c.actors["accountant"],c.book["id"],bank["id"])
    d=c.ledger.update_draft(c.actors["accountant"],c.book["id"],bank["draft_id"],DraftUpdate(expected_version=value["draft_version"],description=value["description"],lines=value["lines"],correction_reason="核销的清偿不推广到普通分类"))
    c.ledger.approve(c.actors["accountant"],c.book["id"],d["id"],ApprovalRequest(expected_version=d["version"]))
    learning=LearningService(c.db)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        sample=dict(conn.execute("SELECT * FROM learning_samples WHERE draft_id=?",(d["id"],)).fetchone())
    definition=RuleDefinition(name="清偿不能推广",source_kind="bank",conditions={"business_type":"daily_cashflow","direction":"expense","payment_status":"paid","category":None,"counterparty":"耗材商店"},lines=[{"account_code":item["clearing_account"],"side":"debit","amount_field":"gross"},{"account_code":"1002","side":"credit","amount_field":"gross"}])
    return learning,sample,definition


def test_pg_clearance_sample_kept_and_no_automatic_rule(pg_ai):
    c=pg_ai
    learning,sample,definition=cleared(c)
    detail=learning.get_sample(c.actors["accountant"],c.book["id"],sample["id"])
    assert not detail["ordinary_rule_eligible"] and detail["corrected"]
    assert not any(sample["id"] in row["sample_ids"] for row in learning.overview(c.actors["accountant"],c.book["id"])["rules"])
    from app.accounting.domain import AccountingError
    with pytest.raises(AccountingError,match="不能用于普通分类"):
        learning.save_rule(c.actors["accountant"],c.book["id"],RuleCreate(definition=definition,sample_ids=[sample["id"]],reason="不能推广付款清偿"))
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert not conn.execute("SELECT accounting.learning_sample_ordinary(?,?)",(c.book["id"],sample["id"])).fetchone()[0]


def test_pg_native_version_guard_rejects_clearance_positive(pg_ai):
    c=pg_ai
    learning,sample,definition=cleared(c)
    rule_id=uuid4().hex
    with pytest.raises(c.psycopg.errors.CheckViolation,match="Clearance samples"):
        with c.db.transaction(write=True,actor=c.actors["accountant"]) as conn:
            conn.execute("INSERT INTO learning_rules(id,firm_id,company_id,book_id,period,current_version,state_version,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,1,1,'draft',?,?,?)",(rule_id,c.manager.firm_id,c.company["id"],c.book["id"],"2026-10",c.actors["accountant"].user_id,now(),now()))
            conn.execute("INSERT INTO learning_rule_versions VALUES(?,?,?,?,?,?,?,?,?)",(rule_id,c.book["id"],1,encode(definition.model_dump()),fingerprint(definition.model_dump()),encode([sample["id"]]),"SQL不能推广核销样例",c.actors["accountant"].user_id,now()))
