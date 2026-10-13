from uuid import uuid4
import json
import sqlite3
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest,DraftUpdate
from app.accounting.service import fingerprint,now
from app.learning.models import RuleCreate,RuleDefinition,RuleEvaluate,RulePublish
from app.learning.service import LearningService,encode
from test_business import business
from test_stage10_settlements import setup,allocate
from test_stage8_learning import bank_sample


def reviewed_clearance(c):
    service,original,item,bank=setup(c)
    learning=LearningService(c.db)
    c.ledger.learning=learning
    allocate(c,service,item,bank)
    d=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    c.ledger.approve(c.actors["accountant"],c.book["id"],d["id"],ApprovalRequest(expected_version=d["version"]))
    clearance=service.clearance(c.actors["accountant"],c.book["id"],bank["id"])
    d=c.ledger.update_draft(c.actors["accountant"],c.book["id"],bank["draft_id"],DraftUpdate(expected_version=clearance["draft_version"],description=clearance["description"],lines=clearance["lines"],correction_reason="应付清偿必须绑定本业务与分配"))
    c.ledger.approve(c.actors["accountant"],c.book["id"],d["id"],ApprovalRequest(expected_version=d["version"]))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        sample=dict(conn.execute("SELECT * FROM learning_samples WHERE draft_id=?",(d["id"],)).fetchone())
    definition=RuleDefinition(name="禁止推广的应付清偿",source_kind="bank",conditions={"business_type":"daily_cashflow","direction":"expense","payment_status":"paid","category":None,"counterparty":"耗材商店"},lines=[{"account_code":item["clearing_account"],"side":"debit","amount_field":"gross"},{"account_code":"1002","side":"credit","amount_field":"gross"}])
    return learning,sample,definition


def ordinary(c,learning):
    positive=bank_sample(c.ledger,learning,c.book["id"],c.actors["accountant"],external="ordinary-positive",counterparty="普通业务供应商甲")
    negative=bank_sample(c.ledger,learning,c.book["id"],c.actors["accountant"],external="ordinary-negative",counterparty="普通业务供应商乙",corrected=False)
    rule=next(row for row in learning.overview(c.actors["accountant"],c.book["id"])["rules"] if positive["id"] in row["sample_ids"])
    return positive,negative,rule


def test_reviewed_clearance_sample_is_readable_but_never_auto_rule_or_positive(business):
    c=business
    learning,sample,definition=reviewed_clearance(c)
    detail=learning.get_sample(c.actors["accountant"],c.book["id"],sample["id"])
    assert detail["corrected"] and not detail["ordinary_rule_eligible"]
    assert detail["original"]["lines"]!=detail["final"]["lines"]
    assert "清偿" in detail["rule_scope_reason"]
    assert not any(sample["id"] in rule["sample_ids"] for rule in learning.overview(c.actors["accountant"],c.book["id"])["rules"])
    with pytest.raises(AccountingError,match="不能用于普通分类"):
        learning.save_rule(c.actors["accountant"],c.book["id"],RuleCreate(definition=definition,sample_ids=[sample["id"]],reason="不能把核销当分类"))


def test_clearance_cannot_be_explicit_negative_but_normal_rule_still_evaluates(business):
    c=business
    learning,sample,_=reviewed_clearance(c)
    positive,negative,rule=ordinary(c,learning)
    with pytest.raises(AccountingError,match="不能用于普通分类"):
        learning.evaluate(c.actors["accountant"],c.book["id"],rule["id"],RuleEvaluate(expected_version=1,negative_sample_ids=[sample["id"]]))
    evaluation=learning.evaluate(c.actors["accountant"],c.book["id"],rule["id"],RuleEvaluate(expected_version=1,negative_sample_ids=[negative["id"]]))
    assert evaluation["passed"]
    published=learning.publish(c.actors["accountant"],c.book["id"],rule["id"],RulePublish(expected_version=1,expected_state_version=1,evaluation_id=evaluation["id"],reason="普通费用样例正常评估发布"))
    assert published["status"]=="published"


def test_sqlite_native_rule_version_and_evaluation_reject_clearance_samples(business):
    c=business
    learning,sample,definition=reviewed_clearance(c)
    positive,negative,rule=ordinary(c,learning)
    with pytest.raises(sqlite3.IntegrityError,match="Clearance samples"):
        with c.db.transaction(write=True) as conn:
            conn.execute("INSERT INTO learning_rule_versions(rule_id,book_id,version,definition_json,definition_fingerprint,sample_ids_json,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(rule["id"],c.book["id"],2,encode(definition.model_dump()),fingerprint(definition.model_dump()),encode([sample["id"]]),"伪造清偿规则版本",c.actors["accountant"].user_id,now()))
    with pytest.raises(sqlite3.IntegrityError,match="Clearance samples"):
        with c.db.transaction(write=True) as conn:
            conn.execute("INSERT INTO learning_rule_evaluations(id,rule_id,book_id,rule_version,negative_sample_ids_json,input_fingerprint,report_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(uuid4().hex,rule["id"],c.book["id"],1,encode([sample["id"]]),"a"*64,encode({"passed":True}),c.actors["accountant"].user_id,now()))


def test_historical_unsafe_rule_cannot_publish_or_generate_unrelated_bank_draft(business):
    c=business
    learning,sample,definition=reviewed_clearance(c)
    _,negative,_=ordinary(c,learning)
    rule_id,evaluation_id=uuid4().hex,uuid4().hex
    # Simulate a rule/evaluation created before the forward guard. Do not
    # modify immutable evidence; restore each trigger inside this transaction.
    with c.db.transaction(write=True) as conn:
        saved={row["name"]:row["sql"] for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE name IN ('learning_ordinary_version','learning_ordinary_evaluation')")}
        for name in saved:
            conn.execute("DROP TRIGGER "+name)
        conn.execute("INSERT INTO learning_rules(id,firm_id,company_id,book_id,period,current_version,state_version,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,1,1,'draft',?,?,?)",(rule_id,c.manager.firm_id,c.company["id"],c.book["id"],"2026-10",c.actors["accountant"].user_id,now(),now()))
        conn.execute("INSERT INTO learning_rule_versions VALUES(?,?,?,?,?,?,?,?,?)",(rule_id,c.book["id"],1,encode(definition.model_dump()),fingerprint(definition.model_dump()),encode([sample["id"]]),"旧清偿推广候选",c.actors["accountant"].user_id,now()))
        conn.execute("INSERT INTO learning_rule_evaluations VALUES(?,?,?,?,?,?,?,?,?)",(evaluation_id,rule_id,c.book["id"],1,encode([negative["id"]]),"a"*64,encode({"passed":True}),c.actors["accountant"].user_id,now()))
        for sql in saved.values():
            conn.execute(sql)
    with pytest.raises(AccountingError,match="不能用于普通分类"):
        learning.publish(c.actors["accountant"],c.book["id"],rule_id,RulePublish(expected_version=1,expected_state_version=1,evaluation_id=evaluation_id,reason="旧评估不得推广清偿"))
    with pytest.raises(sqlite3.IntegrityError,match="Clearance samples"):
        with c.db.transaction(write=True) as conn:
            conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1,evaluation_id=? WHERE id=?",(evaluation_id,rule_id))
    with c.db.transaction(write=True) as conn:
        guard=conn.execute("SELECT sql FROM sqlite_master WHERE name='learning_ordinary_publish'").fetchone()[0]
        conn.execute("DROP TRIGGER learning_ordinary_publish")
        conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1,evaluation_id=? WHERE id=?",(evaluation_id,rule_id))
        conn.execute(guard)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        payload=json.loads(sample["snapshot_json"])["payload"]|{"amount":"1.00"}
        assert learning.rule_generate_in_transaction(conn,c.actors["accountant"],c.book["id"],"daily_cashflow",payload,100,source_kind="bank") is None
