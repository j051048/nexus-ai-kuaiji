"""本地演示身份必须自带一位独立复核人：制单人不能自审、自锁、自发。"""

from app.accounting.database import Database
from app.accounting.domain import Actor, AccountingError
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService
from app.business.demo import PERSONAS, bootstrap_business_demo


def _service(tmp_path):
    accounting = AccountingService(Database(tmp_path / "demo.db"))
    bootstrap(accounting, seed_examples=True)
    bootstrap_business_demo(accounting.db, True)
    return accounting


def test_reviewer_persona_is_seeded_with_review_capability(tmp_path):
    accounting = _service(tmp_path)
    reviewer = Actor(PERSONAS["reviewer"]["user_id"], LOCAL_ACTOR.firm_id)
    with accounting.db.transaction(actor=reviewer) as conn:
        books = conn.execute(
            "SELECT b.id FROM books b WHERE b.firm_id=? ORDER BY b.id", (LOCAL_ACTOR.firm_id,)
        ).fetchall()
        assert books, "demo bootstrap must create example books"
        for row in books:
            accounting.access.book(conn, reviewer, row["id"], "review")
        assert accounting.access.firm_role(conn, reviewer) == "reviewer"


def test_accountant_and_reviewer_are_distinct_identities(tmp_path):
    accounting = _service(tmp_path)
    reviewer = Actor(PERSONAS["reviewer"]["user_id"], LOCAL_ACTOR.firm_id)
    assert reviewer.user_id != LOCAL_ACTOR.user_id
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        book = conn.execute(
            "SELECT id FROM books WHERE firm_id=? ORDER BY id LIMIT 1", (LOCAL_ACTOR.firm_id,)
        ).fetchone()
        accounting.access.book(conn, LOCAL_ACTOR, book["id"], "edit")
    # 负责人是管理者，能进复核通道；但真正能不能复核某张凭证由复核政策判定，
    # 演示身份的价值就是给政策一个“另一个人”，而不是给负责人换角色。
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        accounting.access.book(conn, LOCAL_ACTOR, book["id"], "review")
