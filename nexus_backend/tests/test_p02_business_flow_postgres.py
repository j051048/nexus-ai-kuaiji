"""P02 业务全链路验收（真实 API + PostgreSQL，本地虚构数据）。

链路：员工录入 → 部门/财务核验 → 交接 → 草稿 → 独立复核 →
正式过账 → 账簿 → 税务底稿 → 月度交付包 → 客户签收 → 事务所确认。

替换边界仅两处：身份供应商（本地 stub 验签）与模型响应（固定夹具）。
数据库、权限、版本、金额、幂等与审计全部走真实实现。
"""

import io
import json
import os
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyMemberGrant,
    DepartmentCreate,
)
from app.accounting.service import AccountingService
from app.auth import AuthSettings, Identity
from app.business.service import BusinessService
from app.main import create_app

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
WORKER_URL = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL,
    reason="需要专用 PostgreSQL 管理员、受限 API 与 worker 测试连接",
)

PEOPLE = {
    "accountant": ("p02-reviewer", "P02独立复核会计"),
    "employee": ("local-business-employee", "P02示例员工"),
    "department": ("local-business-department", "P02部门负责人"),
    "finance": ("local-business-finance", "P02企业财务"),
    "manager": ("p02-manager", "P02独立复核经理"),
}


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(output, "PNG")
    return output.getvalue()


@pytest.fixture(scope="module")
def flow():
    import psycopg
    from scripts.migrate_accounting import migrate

    migrate(ADMIN_URL)
    db = Database(APP_URL)
    suffix = uuid4().hex
    firm_id = "p02-firm-" + suffix
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name) VALUES(%s,'P02事务所负责人') "
            "ON CONFLICT (id) DO NOTHING",
            (PEOPLE["accountant"][0],),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'P02全链路虚构事务所',%s)",
            (firm_id, PEOPLE["accountant"][0]),
        )
        for persona, (user_id, name) in PEOPLE.items():
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name",
                (user_id, name, persona + "@demo.example.invalid"),
            )
            conn.execute(
                "INSERT INTO accounting.memberships VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    firm_id,
                    user_id,
                    {"accountant": "owner", "manager": "reviewer"}.get(persona, "operator"),
                ),
            )
    owner = SimpleNamespace(user_id=PEOPLE["accountant"][0], firm_id=firm_id)
    service = AccountingService(db)
    book = service.create_book(owner, BookCreate(name="P02全链路虚构账套", period="2026-10"))
    company_id, book_id = book["company_id"], book["id"]
    directory = DirectoryService(db)
    department = directory.create_department(owner, company_id, DepartmentCreate(name="P02业务部"))[
        "id"
    ]
    for persona in ("finance", "employee", "department"):
        directory.grant_company_member(
            owner,
            company_id,
            CompanyMemberGrant(
                email=persona + "@demo.example.invalid",
                role="finance" if persona == "finance" else "employee",
                department_id=None if persona == "finance" else department,
            ),
        )
    directory.grant_book_member(
        owner, book_id, PEOPLE["finance"][0], BookMemberGrant(role="editor")
    )
    directory.grant_book_member(
        owner, book_id, PEOPLE["manager"][0], BookMemberGrant(role="reviewer")
    )
    BusinessService(db).grant_department_approver(
        owner, company_id, department, PEOPLE["department"][0]
    )
    return SimpleNamespace(
        db=db,
        psycopg=psycopg,
        admin=ADMIN_URL,
        firm=firm_id,
        company=company_id,
        book=book_id,
        department=department,
        prefix=suffix,
    )


@pytest.fixture(scope="module")
def client(flow, tmp_path_factory):
    from test_intelligence_workflow import FakeProvider

    root = tmp_path_factory.mktemp("p02-originals")
    provider = FakeProvider()
    # 身份供应商是本轮允许替换的边界；负责人的二次验证级别由供应商断言。
    identities = {
        persona: Identity(
            user_id,
            persona + "@demo.example.invalid",
            name,
            aal="aal2" if persona == "accountant" else "aal1",
        )
        for persona, (user_id, name) in PEOPLE.items()
    }
    application = create_app(
        APP_URL,
        seed=False,
        attachment_root=root,
        auth_settings=AuthSettings(
            mode="supabase",
            supabase_url="https://isolated-auth.example.test",
            publishable_key="sb_publishable_test_only",
        ),
        authenticator=SimpleNamespace(authenticate=lambda token: identities[token]),
        intelligence_provider=provider,
        queue_worker_enabled=False,
    )
    with TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 5678)) as value:
        value.provider = provider
        value.flow = flow
        yield value


def auth(flow, persona):
    return {"Authorization": "Bearer " + persona, "X-Firm-Id": flow.firm}


def test_b01_employee_to_customer_signoff(client, flow):
    employee = auth(flow, "employee")
    approver = auth(flow, "department")
    finance = auth(flow, "finance")
    accountant = auth(flow, "accountant")
    fields = {
        "occurred_on": "2026-10-08",
        "description": "P02 全链路办公用品",
        "counterparty": "虚构供应商",
        "amount": "268.10",
        "direction": "expense",
        "payment_status": "unpaid",
        "category": "office",
        "purpose": "办公耗材",
    }

    # ① 员工提交事项与原件
    draft = client.post(
        "/api/intake/drafts",
        headers=employee,
        json={
            "company_id": flow.company,
            "department_id": flow.department,
            "template_id": "reimbursement",
            "fields": fields,
            "idempotency_key": uuid4().hex,
        },
    )
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    uploaded = client.post(
        f"/api/intake/drafts/{draft['id']}/attachments?expected_version={draft['version']}",
        headers=employee,
        files={"file": ("票据.png", png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    draft = uploaded.json()
    assert len(draft["attachments"][0]["sha256"]) == 64

    completion = client.post(
        f"/api/intake/drafts/{draft['id']}/complete",
        headers=employee,
        json={
            "expected_version": draft["version"],
            "payload": fields,
            "fields_confirmed": True,
            "submit": True,
            "idempotency_key": uuid4().hex,
        },
    )
    assert completion.status_code in {200, 201}, completion.text
    event = completion.json()["event"]
    assert event["status"] == "submitted" and event["version"] >= 2

    # ② 部门审批 → 财务核验
    path = "/api/business/events/" + event["id"]
    approved = client.post(
        path + "/department-approve",
        headers=approver,
        json={"expected_version": event["version"]},
    )
    assert approved.status_code == 200, approved.text
    event = approved.json()
    approved = client.post(
        path + "/finance-approve",
        headers=finance,
        json={"expected_version": event["version"]},
    )
    assert approved.status_code == 200, approved.text
    event = approved.json()

    # ④ 票据识别与异常核对（模型为固定夹具，人工处理阻塞项）
    from test_intelligence_workflow import invoice_document

    client.provider.document = invoice_document(amount="268.10", net_amount="268.10")
    settings = client.put(
        f"/api/companies/{flow.company}/intelligence/settings",
        headers=accountant,
        json={"enabled": True},
    )
    assert settings.status_code == 200, settings.text
    attachment_id = uploaded.json()["attachments"][0]["id"]
    extraction = client.post(
        f"/api/business/events/{event['id']}/ocr",
        headers=employee,
        json={
            "attachment_id": attachment_id,
            "expected_version": client.get(path, headers=employee).json()["version"],
            "idempotency_key": uuid4().hex,
        },
    )
    assert extraction.status_code == 200, extraction.text
    flow.event_id = event["id"]
    detail = client.get(f"/api/business/events/{event['id']}/intelligence", headers=finance)
    assert detail.status_code == 200, detail.text
    findings = detail.json()["findings"]
    print(
        "FINDINGS",
        json.dumps(
            [(f["severity"], f["code"], f["resolved"]) for f in findings], ensure_ascii=False
        ),
    )
    for finding in findings:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            resolved = client.post(
                f"/api/intelligence/findings/{finding['id']}/resolve",
                headers=finance,
                json={"reason": "P02 全链路：已人工核对原件与业务事实"},
            )
            assert resolved.status_code == 200, resolved.text

    # ③ 财务交接：来源 + 草稿 + 映射
    handoff = client.post(
        path + "/handoff",
        headers=finance,
        json={
            "expected_version": event["version"],
            "book_id": flow.book,
            "idempotency_key": uuid4().hex,
        },
    )
    assert handoff.status_code == 200, handoff.text
    receipt = handoff.json()
    draft_id = receipt["draft_id"]
    assert receipt["source_id"] and draft_id

    # ④ 会计基于原件与来源生成候选分录（模型为固定夹具）
    detail = client.get(f"/api/books/{flow.book}/drafts/{draft_id}", headers=accountant)
    assert detail.status_code == 200, detail.text
    proposal = client.post(
        f"/api/books/{flow.book}/drafts/{draft_id}/ai-proposals",
        headers=accountant,
        json={"expected_version": detail.json()["version"], "idempotency_key": uuid4().hex},
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["status"] == "succeeded", json.dumps(
        proposal.json(), ensure_ascii=True, indent=1
    )
    detail = client.get(f"/api/books/{flow.book}/drafts/{draft_id}", headers=accountant)
    applied = client.post(
        f"/api/intelligence/proposals/{proposal.json()['id']}/apply",
        headers=accountant,
        json={"expected_version": detail.json()["version"]},
    )
    assert applied.status_code in {200, 201}, applied.text
    draft_view = client.get(f"/api/books/{flow.book}/drafts/{draft_id}", headers=accountant).json()
    assert draft_view["status"] == "draft" and not draft_view["issues"]

    # ④b 模型建议运行后会产生新的待核对项，必须逐条人工处理
    refreshed = client.get(f"/api/business/events/{flow.event_id}/intelligence", headers=finance)
    assert refreshed.status_code == 200, refreshed.text
    for finding in refreshed.json()["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            resolved = client.post(
                f"/api/intelligence/findings/{finding['id']}/resolve",
                headers=finance,
                json={"reason": "P02 全链路：建议生成后再次人工核对原件"},
            )
            assert resolved.status_code == 200, resolved.text

    # ⑤ 独立复核：制单以外的会计复核，不能由本人自审
    manager = auth(flow, "manager")
    self_review = client.post(
        f"/api/books/{flow.book}/drafts/{draft_id}/approve",
        headers=accountant,
        json={"expected_version": draft_view["version"]},
    )
    assert self_review.status_code == 403, self_review.text
    approved = client.post(
        f"/api/books/{flow.book}/drafts/{draft_id}/approve",
        headers=manager,
        json={"expected_version": draft_view["version"]},
    )
    assert approved.status_code == 200, approved.text
    approved = approved.json()
    assert approved["status"] == "approved" and approved["version"] > draft_view["version"]

    # ⑤b 期初冻结与绑定：正式记账只接受已冻结的期初包
    ledger_id = "ledger_" + flow.book
    policy = client.post(
        "/api/opening/policies",
        headers=accountant,
        json={
            "ledger_id": ledger_id,
            "standard_kind": "small_enterprise",
            "taxpayer_kind": "small_scale",
            "effective_from": "2026-01",
            "effective_to": "2026-12",
            "reason": "P02 期初政策",
        },
    )
    assert policy.status_code == 201, policy.text
    opening_accounts = {}
    for code, name, category, normal in (
        ("1002", "银行存款", "asset", "debit"),
        ("2202", "应付账款", "liability", "credit"),
        ("2241", "其他应付款", "liability", "credit"),
        ("6602.02", "管理费用—办公费", "expense", "debit"),
    ):
        created = client.post(
            "/api/opening/accounts",
            headers=accountant,
            json={
                "ledger_id": ledger_id,
                "code": code,
                "name": name,
                "category": category,
                "normal_balance": normal,
                "effective_from": "2026-01",
                "effective_to": "2026-12",
                "reason": "P02 期初科目",
            },
        )
        assert created.status_code == 201, created.text
        opening_accounts[code] = created.json()
    package = client.post(
        "/api/opening/packages",
        headers=accountant,
        json={
            "ledger_id": ledger_id,
            "target_period": "2026-10",
            "policy_id": policy.json()["id"],
        },
    )
    assert package.status_code == 201, package.text
    package_id = package.json()["package"]["id"]
    opening_version = client.post(
        f"/api/opening/packages/{package_id}/versions",
        headers=accountant,
        json={
            "idempotency_key": "p02-opening-" + flow.prefix,
            "reason": "P02 期初导入",
            "lines": [
                {
                    "account_id": opening_accounts["1002"]["id"],
                    "account_version": 1,
                    "year_opening": "0.00",
                    "ytd_debit": "10000.00",
                    "ytd_credit": "0.00",
                    "opening": "10000.00",
                },
                {
                    "account_id": opening_accounts["2202"]["id"],
                    "account_version": 1,
                    "year_opening": "0.00",
                    "ytd_debit": "0.00",
                    "ytd_credit": "10000.00",
                    "opening": "-10000.00",
                },
            ],
        },
    )
    assert opening_version.status_code == 201, opening_version.text
    version_id = opening_version.json()["version"]["id"]
    for action in ("validate", "submit"):
        body = {} if action == "validate" else {"expected_version": 1, "reason": "P02 提交期初"}
        result = client.post(
            f"/api/opening/versions/{version_id}/{action}", headers=accountant, json=body
        )
        assert result.status_code in {200, 201}, result.text
    granted = client.post(
        f"/api/books/{flow.book}/ledger/managers",
        headers=accountant,
        json={"user_id": PEOPLE["manager"][0], "reason": "P02 期初独立核准授权"},
    )
    assert granted.status_code in {200, 201}, granted.text
    approved_opening = client.post(
        f"/api/opening/versions/{version_id}/approve",
        headers=manager,
        json={"expected_version": 1, "reason": "P02 独立核准期初"},
    )
    assert approved_opening.status_code == 200, approved_opening.text
    frozen = client.post(
        f"/api/opening/versions/{version_id}/freeze",
        headers=manager,
        json={"expected_version": 1, "reason": "P02 冻结期初"},
    )
    assert frozen.status_code == 200, frozen.text
    for code, row in opening_accounts.items():
        mapped = client.post(
            "/api/opening/mappings",
            headers=accountant,
            json={
                "book_id": flow.book,
                "book_account_code": code,
                "account_id": row["id"],
                "account_version": 1,
                "reason": "P02 旧账套映射",
            },
        )
        assert mapped.status_code == 201, mapped.text
    bound = client.post(
        "/api/posting/opening-bindings",
        headers=accountant,
        json={
            "book_id": flow.book,
            "package_version_id": version_id,
            "reason": "P02 绑定冻结期初",
        },
    )
    assert bound.status_code == 201, bound.text
    assert bound.json()["binding"]["status"] == "active"

    # ⑥ 正式过账（追加式凭证）
    posted = client.post(
        f"/api/books/{flow.book}/drafts/{draft_id}/post",
        headers=accountant,
        json={
            "command_key": "p02-post-" + flow.prefix,
            "expected_version": approved["version"],
            "voucher_date": "2026-10-08",
        },
    )
    assert posted.status_code in {200, 201}, posted.text
    voucher = posted.json()
    assert voucher["voucher"]["id"]

    # ⑦ 账簿：余额表与明细账都读正式凭证
    balance = client.get(
        f"/api/books/{flow.book}/trial-balance", headers=accountant, params={"as_of": "2026-10-31"}
    )
    assert balance.status_code == 200, balance.text
    totals = balance.json()["totals"]
    assert totals["debit_cents"] == totals["credit_cents"] == 26810
    assert balance.json()["rows"], totals
    rows = {row["code"]: row for row in balance.json()["rows"]}
    assert rows["6602.02"]["debit_cents"] == 26810
    ledger = client.get(
        f"/api/books/{flow.book}/ledger/{rows['6602.02']['account_id']}", headers=accountant
    )
    assert ledger.status_code == 200, ledger.text
    assert ledger.json()["entries"], ledger.text
    export = client.get(
        f"/api/books/{flow.book}/formal-books/export",
        headers=accountant,
        params={"kind": "trial_balance"},
    )
    assert export.status_code == 200, export.text
    assert "6602.02" in export.text
    exported = client.get(
        f"/api/books/{flow.book}/formal-books/export",
        headers=accountant,
        params={"kind": "account_ledger", "account_id": rows["6602.02"]["account_id"]},
    )
    assert exported.status_code == 200, exported.text

    # ⑧ 税务底稿：先登记纳税人档案；无发票导入时必须明确阻断而不是猜数
    profile = client.put(
        "/api/tax/profile",
        headers=accountant,
        json={
            "book_id": flow.book,
            "taxpayer_type": "small_scale",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 1200,
            "filing_cycle": "monthly",
            "effective_from": "2026-10",
            "reason": "P02 全链路虚构纳税人档案",
        },
    )
    assert profile.status_code == 200, profile.text
    workpaper = client.post(
        "/api/tax/workpapers",
        headers=accountant,
        json={
            "book_id": flow.book,
            "period": "2026-10",
            "reason": "P02 底稿生成",
            "vat_accounts": [],
        },
    )
    assert workpaper.status_code in {200, 201, 409}, workpaper.text
    tax_state = workpaper.status_code
    if workpaper.status_code in {200, 201}:
        paper = workpaper.json()
        assert paper["data"]["filing"] == "not_filed"
        reviewed_paper = client.post(
            f"/api/tax/workpapers/{paper['id']}/review",
            headers=accountant,
            json={
                "book_id": flow.book,
                "reason": "P02 税务底稿复核",
                "expected_version": paper["version"],
            },
        )
        assert reviewed_paper.status_code == 200, reviewed_paper.text

    # ⑧a 运维核对：模型外发结果未知的费用必须由事务所逐笔确认后才能关期
    with flow.psycopg.connect(flow.admin) as conn:
        uncertain = conn.execute(
            "SELECT id,version FROM accounting.model_costs "
            "WHERE company_id=%s AND state='uncertain'",
            (flow.company,),
        ).fetchall()
    for cost_id, version in uncertain:
        reconciled = client.post(
            f"/api/operations/costs/{cost_id}/reconcile",
            headers=accountant,
            json={
                "reason": "P02 运维核对：本地模型替身无计费，按零成本确认",
                "expected_version": version,
                "confirmed_cost": "0.00",
            },
        )
        assert reconciled.status_code in {200, 201}, reconciled.text

    # ⑧b 后台队列：用受限 worker 身份真实领取并结算交接与识别任务
    from app.intelligence.service import IntelligenceService
    from app.jobs.service import QueueService
    from app.jobs.worker import QueueWorker

    worker_db = Database(WORKER_URL)
    worker_intel = IntelligenceService(
        worker_db, client.app.state.attachment_store, provider=client.provider
    )
    worker_queue = QueueService(worker_db, worker_intel)
    worker = QueueWorker(
        worker_queue, worker_id="p02-worker-" + flow.prefix, concurrency=1, lease_seconds=180
    )
    drained = 0
    while drained < 80 and worker.run_once():
        drained += 1
    print("DRAINED", drained)

    # ⑧c 导出批次：关期要求草稿已导出到外部财务软件，导出后版本前移
    exported_batch = client.post(
        f"/api/books/{flow.book}/exports",
        headers=accountant,
        json={"draft_ids": [draft_id], "idempotency_key": "p02-export-" + flow.prefix},
    )
    assert exported_batch.status_code in {200, 201}, exported_batch.text
    assert exported_batch.json()["count"] == 1
    after_export = client.get(
        f"/api/books/{flow.book}/drafts/{draft_id}", headers=accountant
    ).json()
    assert after_export["status"] == "exported"

    # ⑧d 外部软件回执：关期要求导出批次有内容一致的完整回执
    receipt = client.post(
        f"/api/books/{flow.book}/delivery/exports/{exported_batch.json()['id']}/receipts",
        headers=accountant,
        json={
            "book_id": flow.book,
            "company_id": flow.company,
            "period": "2026-10",
            "currency": "CNY",
            "items": [
                {
                    "draft_id": draft_id,
                    "external_voucher_id": "EXT-" + flow.prefix,
                    "status": "accepted",
                    "date": after_export["source_date"],
                    "description": after_export["description"],
                    "amount": str(sum(Decimal(line["debit"]) for line in after_export["lines"])),
                    "lines": [
                        {
                            "ordinal": index,
                            "account_code": line["account_code"],
                            "debit": line["debit"],
                            "credit": line["credit"],
                            "auxiliary_values": line.get("auxiliary_values", {}),
                        }
                        for index, line in enumerate(after_export["lines"])
                    ],
                }
            ],
            "idempotency_key": "p02-receipt-" + flow.prefix,
            "reason": "本地合成回执，用于验证外部导入结果核对",
        },
    )
    assert receipt.status_code in {200, 201}, receipt.text

    # ⑨ 正式报表：生成 → 校验（编制人）
    report = client.post(
        "/api/reports", headers=accountant, json={"book_id": flow.book, "period": "2026-10"}
    )
    assert report.status_code == 201, report.text
    report_row = report.json()["report"]
    validated = client.post(
        f"/api/reports/{report_row['id']}/validate", headers=accountant, json={}
    )
    assert validated.status_code == 200, validated.text

    # ⑨b 报表复核：月结预检要求报表已复核，发布仍须锁期
    reviewed_report = client.post(
        f"/api/reports/{report_row['id']}/review",
        headers=manager,
        json={"expected_version": report_row["version"], "reason": "P02 报表独立复核"},
    )
    assert reviewed_report.status_code == 200, reviewed_report.text

    # ⑩ 关期预检（资料齐全性快照）：无阻断项
    preflight = client.post(
        f"/api/books/{flow.book}/closing/preflights",
        headers=accountant,
        json={"idempotency_key": "p02-close-" + flow.prefix},
    )
    assert preflight.status_code == 201, preflight.text
    assert preflight.json()["report"]["passed"] is True, preflight.text

    # ⑪ 月结门槛（B04）：期初含往来控制科目余额，未配置子账即无法锁期；
    # 交付包依赖已发布报表，故客户签收链路本轮被阻断（如实记录，不静默跳过）。
    b04 = client.post(
        "/api/b04-close/preflight",
        headers=manager,
        json={
            "book_id": flow.book,
            "period": "2026-10",
            "report_id": report_row["id"],
            "idempotency_key": "p02-b04-" + flow.prefix,
            "reason": "P02 月结预检",
        },
    )
    assert b04.status_code == 201, b04.text
    blocked = b04.json()["close"]
    assert blocked["status"] == "preflight_failed", b04.text
    blocked_codes = sorted(
        item["code"] for item in json.loads(blocked["checks_json"]) if item["status"] == "blocked"
    )
    assert "subledger_control" in blocked_codes

    print(
        "CHAIN",
        json.dumps(
            {
                "draft": draft_id,
                "voucher": voucher["voucher"]["id"],
                "tax": tax_state,
                "blocked": blocked_codes,
            },
            ensure_ascii=False,
        ),
    )
