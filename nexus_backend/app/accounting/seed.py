from .models import BookCreate, ImportRequest
from .service import AccountingService, Actor


LOCAL_ACTOR = Actor(user_id="local-accountant", firm_id="local-firm")
FIRM_NAME = "示例会计事务所"
USER_NAME = "本地复核会计"


def bootstrap(service: AccountingService, seed_examples: bool = True):
    with service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(id,name,email) VALUES(?,?,'local@example.invalid')",
            (LOCAL_ACTOR.user_id, USER_NAME),
        )
        conn.execute(
            "INSERT OR IGNORE INTO firms(id,name,created_by) VALUES(?,?,?)",
            (LOCAL_ACTOR.firm_id, FIRM_NAME, LOCAL_ACTOR.user_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO memberships(firm_id,user_id,role) VALUES(?,?,'owner')",
            (LOCAL_ACTOR.firm_id, LOCAL_ACTOR.user_id),
        )
        conn.execute(
            "UPDATE memberships SET role='owner' WHERE firm_id=? AND user_id=?",
            (LOCAL_ACTOR.firm_id, LOCAL_ACTOR.user_id),
        )
        has_books = conn.execute(
            "SELECT 1 FROM books WHERE firm_id=? LIMIT 1", (LOCAL_ACTOR.firm_id,)
        ).fetchone()
    if not seed_examples or has_books:
        return
    examples = [
        (
            "澄光科技（虚构示例）",
            [
                (
                    "bank-a:202610:001",
                    "办公室房租",
                    "示例园区",
                    "8500.00",
                    "expense",
                    "示例租赁合同 / 2026-10",
                ),
                (
                    "bank-a:202610:002",
                    "办公用品采购",
                    "示例文具店",
                    "368.50",
                    "expense",
                    "示例发票 DEMO-002",
                ),
                (
                    "bank-a:202610:003",
                    "项目服务收入",
                    "示例客户甲",
                    "18000.00",
                    "income",
                    "示例服务合同 DEMO-003",
                ),
            ],
        ),
        (
            "禾田商贸（虚构示例）",
            [
                (
                    "bank-b:202610:001",
                    "日常款项往来",
                    "示例往来方",
                    "2600.00",
                    "expense",
                    "示例付款说明 DEMO-004",
                ),
                ("bank-b:202610:002", "办公设备租金", "示例租赁公司", "1200.00", "expense", ""),
            ],
        ),
        (
            "青屿设计（虚构示例）",
            [
                (
                    "bank-c:202610:001",
                    "市内交通费",
                    "示例出行服务",
                    "126.80",
                    "expense",
                    "示例行程单 DEMO-006",
                ),
                (
                    "bank-c:202610:002",
                    "宽带通信费",
                    "示例网络服务",
                    "299.00",
                    "expense",
                    "示例缴费回执 DEMO-007",
                ),
            ],
        ),
    ]
    for name, rows in examples:
        book = service.create_book(LOCAL_ACTOR, BookCreate(name=name, period="2026-10"))
        request = ImportRequest(
            source_name="内置虚构示例",
            rows=[
                {
                    "external_id": row[0],
                    "date": f"2026-10-{index + 1:02d}",
                    "description": row[1],
                    "counterparty": row[2],
                    "amount": row[3],
                    "direction": row[4],
                    "evidence_ref": row[5],
                }
                for index, row in enumerate(rows)
            ],
        )
        service.import_sources(LOCAL_ACTOR, book["id"], request)
        service.suggestions(LOCAL_ACTOR, book["id"])
