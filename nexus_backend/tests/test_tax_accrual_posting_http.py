"""四税计提入账：走既有「手工凭证 → 独立复核 → 过账」链路的 HTTP 集成测试。

不新建第二套凭证入口。这里验证的每一步——首次成功、重复点击幂等、复核人批准过账、
制单人自审被拒——都发生在既有的 ``/api/posting/manual-requests`` 语义之内，因此
独立复核、幂等键与月结 ``unposted_manual`` 门禁（按 ``manual_review_requests`` 与
``vouchers.review_request_id`` 关联计数）自动复用。
"""

import json
import secrets
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.accounting.models import BookMemberGrant, FirmMemberGrant
from app.accounting.seed import LOCAL_ACTOR
from app.auth.supabase import Identity
from app.main import create_app
from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from policy_fixtures import approve_policy_independently

PERIOD = "2026-10"
PERIOD_END = "2026-10-31"
#: 附加税与所得税的默认计提科目；缺一个就必须报 409，而不是记到不存在的科目。
ACCRUAL_ACCOUNTS = {
    "6403": ("税金及附加", "expense", "debit"),
    "6801": ("所得税费用", "expense", "debit"),
    "2221.03": ("应交城建税及附加", "liability", "credit"),
    "2221.05": ("应交企业所得税", "liability", "credit"),
}


def _book(app):
    with app.state.service.db.transaction() as conn:
        return conn.execute("SELECT id,company_id,firm_id,period FROM books LIMIT 1").fetchone()


def _firm_identity(app, role, prefix):
    """Register a real second identity and mint its token (no fixture bypass)."""
    identity = SimpleNamespace(
        user_id=f"{prefix}-{secrets.token_hex(4)}",
        display_name=f"{prefix}身份",
        email=f"{prefix}-{secrets.token_hex(4)}@example.test",
    )
    app.state.directory.register_identity(identity)
    app.state.directory.grant_firm_member(
        LOCAL_ACTOR, FirmMemberGrant(email=identity.email, role=role)
    )
    token = secrets.token_urlsafe(32)
    app.state.tokens[token] = time.monotonic() + 7200
    app.state.token_users[token] = Identity(identity.user_id, identity.email, identity.display_name)
    return identity, {"Authorization": "Bearer " + token}


def _drop_invoice(app, book, day="05"):
    rows = [
        {
            "date": f"{book['period']}-{day}",
            "direction": "output",
            "amount_cents": 100_000,
            "tax_cents": 3_000,
            "total_cents": 103_000,
            "counterparty": "客户甲",
        }
    ]
    with app.state.service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,storage_key,"
            "sha256,size_bytes,row_count,direction,rows_json,notes_json,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "batch-accrual-http",
                book["id"],
                book["firm_id"],
                book["company_id"],
                "2026-10.xlsx",
                "invoice/accrual.xlsx",
                "b" * 64,
                1024,
                len(rows),
                "mixed",
                json.dumps(rows, ensure_ascii=False),
                "[]",
                LOCAL_ACTOR.user_id,
                "2026-10-05T00:00:00+00:00",
            ),
        )


def _bound_book(app, accrual_windows=None):
    """Freeze an opening package and bind it, using a second identity to approve it.

    ``accrual_windows`` overrides the effective window of an accrual account, so a
    test can build a chart where that code is genuinely absent for the period.
    """
    book = _book(app)
    with app.state.service.db.transaction() as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()["ledger_id"]
    opening = app.state.opening
    policy = opening.create_policy(
        LOCAL_ACTOR,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            formal_book_system="local",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="计提入账 HTTP 政策",
        ),
    )
    approve_policy_independently(opening, app.state.service.db, LOCAL_ACTOR, policy["id"])

    def account(code, name, category, normal, window=("2026-01", "2026-12")):
        return opening.create_account(
            LOCAL_ACTOR,
            AccountCreate(
                ledger_id=ledger_id,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from=window[0],
                effective_to=window[1],
                reason="计提入账 HTTP 科目",
            ),
        )

    bank = account("1002", "银行存款", "asset", "debit")
    payable = account("2202", "应付账款", "liability", "credit")
    vat = account("222101", "应交增值税", "liability", "credit")
    revenue = account("6001", "主营业务收入", "income", "credit")
    accrual = {
        code: account(
            code,
            name,
            category,
            normal,
            (accrual_windows or {}).get(code, ("2026-01", "2026-12")),
        )
        for code, (name, category, normal) in ACCRUAL_ACCOUNTS.items()
    }
    package = opening.create_package(
        LOCAL_ACTOR,
        OpeningPackageCreate(
            ledger_id=ledger_id, target_period=book["period"], policy_id=policy["id"]
        ),
    )
    version = opening.import_version(
        LOCAL_ACTOR,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="accrual-http-opening",
            reason="计提入账 HTTP 期初",
            lines=[
                OpeningLineInput(
                    account_id=bank["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="10000.00",
                    ytd_credit="0.00",
                    opening="10000.00",
                ),
                OpeningLineInput(
                    account_id=payable["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit="10000.00",
                    opening="-10000.00",
                ),
            ],
        ),
    )
    version_id = version["version"]["id"]
    opening.validate_version(LOCAL_ACTOR, version_id)
    opening.submit_version(
        LOCAL_ACTOR,
        version_id,
        OpeningCommand(expected_version=1, reason="提交计提入账 HTTP 期初"),
    )
    admin, _ = _firm_identity(app, "admin", "accrual-admin")
    approver = Actor(admin.user_id, LOCAL_ACTOR.firm_id)
    opening.approve_version(
        approver, version_id, OpeningCommand(expected_version=1, reason="独立核准计提入账期初")
    )
    opening.freeze_version(
        approver, version_id, OpeningCommand(expected_version=1, reason="冻结计提入账期初")
    )
    return {
        "book": book,
        "ledger_id": ledger_id,
        "version_id": version_id,
        "bank": bank,
        "payable": payable,
        "vat": vat,
        "revenue": revenue,
        "accrual": accrual,
    }


def _reviewed_workpaper(client, app, setup, headers, reviewer_headers):
    """Profile → invoice import → workpaper → independent review (all through HTTP)."""
    book = setup["book"]
    profile = client.put(
        "/api/tax/profile",
        headers=headers,
        json={
            "book_id": book["id"],
            "taxpayer_type": "small_scale",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 1200,
            "filing_cycle": "monthly",
            "effective_from": "2026-01",
            "reason": "计提入账 HTTP 档案",
        },
    )
    assert profile.status_code == 200, profile.text
    _drop_invoice(app, book)
    created = client.post(
        "/api/tax/workpapers",
        headers=headers,
        json={
            "book_id": book["id"],
            "period": book["period"],
            "reason": "计提入账 HTTP 底稿",
            # 只映射本期生效的增值税科目；计提科目由入账接口按期间自行解析，
            # 不进底稿的科目映射（底稿只关心销项与应纳增值税）。
            "vat_accounts": [
                {"account_id": setup["vat"]["id"], "account_version": 1, "role": "vat_payable"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    paper = created.json()
    assert paper["status"] == "draft"
    reviewed = client.post(
        f"/api/tax/workpapers/{paper['id']}/review",
        headers=reviewer_headers,
        json={
            "book_id": book["id"],
            "reason": "独立核验本地虚构票账差异",
            "expected_version": paper["version"],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"
    return reviewed.json()


def _accrual_body(setup):
    # 不传 surtax_items：附加税依据取自本期税务档案（与底稿同一税率），
    # 服务端会在 surtax_basis 里说明依据并校验与底稿登记值一致。
    return {
        "book_id": setup["book"]["id"],
        "period": PERIOD,
        "cumulative_profit_cents": 100_000,
    }


def test_accrual_request_posts_only_after_independent_review(tmp_path):
    app = create_app(tmp_path / "tax-accrual-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45693)) as client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        setup = _bound_book(app)
        book = setup["book"]
        bound = client.post(
            "/api/posting/opening-bindings",
            headers=headers,
            json={
                "book_id": book["id"],
                "package_version_id": setup["version_id"],
                "reason": "绑定计提入账 HTTP 期初",
            },
        )
        assert bound.status_code == 201, bound.text
        reviewer, reviewer_headers = _firm_identity(app, "reviewer", "accrual-reviewer")
        app.state.directory.grant_book_member(
            LOCAL_ACTOR, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
        )
        _reviewed_workpaper(client, app, setup, headers, reviewer_headers)

        # 1) 首次成功：申请进入待复核，凭证本身没有生成。
        #    本期没有印花税合同，因此不要求 2221.04 科目存在——只校验真正要记的科目。
        first = client.post(
            "/api/tax/accrual-manual-request", headers=headers, json=_accrual_body(setup)
        )
        assert first.status_code == 201, first.text
        body = first.json()
        request = body["request"]
        assert request["status"] == "pending"
        assert body["posted"] is False
        # 附加税 3000×12%（档案税率）= 360 分，所得税 100000×25% = 25000 分。
        assert body["surtax_basis"]["source"] == "tax_profile"
        assert body["surtax_basis"]["rate_bp"] == 1200
        assert body["surtax_cents"] == 360
        assert body["voucher"]["debit_cents"] == body["voucher"]["credit_cents"] == 25_360
        assert body["voucher"]["line_count"] == 4
        assert body["voucher"]["command_key"].startswith("tax-accrual:")
        assert body["voucher"]["voucher_date"] == PERIOD_END
        assert "不会直接记账" in body["note"]

        # 2) 重复点击幂等：同一底稿身份得到同一个请求，而不是第二张凭证。
        replay = client.post(
            "/api/tax/accrual-manual-request", headers=headers, json=_accrual_body(setup)
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["request"]["id"] == request["id"]
        assert replay.json()["voucher"]["command_key"] == body["voucher"]["command_key"]
        listed = client.get(
            f"/api/books/{book['id']}/manual-review-requests", headers=headers
        ).json()
        assert [item["id"] for item in listed["requests"]] == [request["id"]]

        # 3) 制单人自审必须被拒，且申请状态不变（没有写出任何东西）。
        self_review = client.post(
            f"/api/posting/manual-requests/{request['id']}/approve",
            headers=headers,
            json={
                "expected_version": request["version"],
                "expected_policy_version": request["policy_version"],
                "reason": "制单人试图批准本人计提申请",
            },
        )
        assert self_review.status_code == 403, self_review.text
        assert "本人" in self_review.json()["detail"]
        still_pending = client.get(
            f"/api/books/{book['id']}/manual-review-requests", headers=headers
        ).json()["requests"][0]
        assert (
            still_pending["status"] == "pending" and still_pending["version"] == request["version"]
        )

        # 4) 独立复核人批准后可过账，凭证通过 review_request_id 与申请挂钩——
        #    这正是月结 unposted_manual 门禁使用的关联键。
        approved = client.post(
            f"/api/posting/manual-requests/{request['id']}/approve",
            headers=reviewer_headers,
            json={
                "expected_version": request["version"],
                "expected_policy_version": request["policy_version"],
                "reason": "独立复核人核对四税计提分录",
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["independent"] is True
        posted = client.post(
            f"/api/posting/manual-requests/{request['id']}/post",
            headers=headers,
            json={
                "expected_version": approved.json()["request"]["version"],
                "command_key": body["voucher"]["command_key"],
            },
        )
        assert posted.status_code == 201, posted.text
        voucher = posted.json()["voucher"]
        assert voucher["review_request_id"] == request["id"]
        assert voucher["voucher_date"] == PERIOD_END
        balance = client.get(f"/api/books/{book['id']}/trial-balance", headers=headers)
        assert balance.status_code == 200, balance.text
        rows = {row["code"]: row for row in balance.json()["rows"]}
        assert rows["6403"]["closing_cents"] == 360
        assert rows["6801"]["closing_cents"] == 25_000
        assert rows["2221.03"]["closing_cents"] == -360
        assert rows["2221.05"]["closing_cents"] == -25_000


def test_accrual_request_refuses_other_period_and_missing_accounts(tmp_path):
    app = create_app(tmp_path / "tax-accrual-guard.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45694)) as client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        # 6801（所得税费用）只在 11 月起生效：本期科目表里没有它。
        setup = _bound_book(app, accrual_windows={"6801": ("2026-11", "2026-12")})
        book = setup["book"]
        bound = client.post(
            "/api/posting/opening-bindings",
            headers=headers,
            json={
                "book_id": book["id"],
                "package_version_id": setup["version_id"],
                "reason": "绑定计提入账 HTTP 期初",
            },
        )
        assert bound.status_code == 201, bound.text
        reviewer, reviewer_headers = _firm_identity(app, "reviewer", "accrual-reviewer-guard")
        app.state.directory.grant_book_member(
            LOCAL_ACTOR, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
        )
        _reviewed_workpaper(client, app, setup, headers, reviewer_headers)

        wrong_period = client.post(
            "/api/tax/accrual-manual-request",
            headers=headers,
            json={**_accrual_body(setup), "period": "2026-09"},
        )
        assert wrong_period.status_code == 409, wrong_period.text
        assert "当前会计期间" in wrong_period.json()["detail"]

        bad_date = client.post(
            "/api/tax/accrual-manual-request",
            headers=headers,
            json={**_accrual_body(setup), "voucher_date": "2026-10-32"},
        )
        assert bad_date.status_code == 422, bad_date.text

        # 科目表里没有 6801：必须点名缺哪个科目，而不是记到相近的科目上。
        missing = client.post(
            "/api/tax/accrual-manual-request", headers=headers, json=_accrual_body(setup)
        )
        assert missing.status_code == 409, missing.text
        assert "6801" in missing.json()["detail"]

        # 显式给的附加税条目必须复现底稿登记值；两套依据不一致时拒绝入账。
        conflicting = client.post(
            "/api/tax/accrual-manual-request",
            headers=headers,
            json={**_accrual_body(setup), "surtax_items": [{"code": "city", "rate_bp": 700}]},
        )
        assert conflicting.status_code == 409, conflicting.text
        assert "不一致" in conflicting.json()["detail"]

        # 没有累计利润就不计提所得税：底稿的账载税额不能当利润用。
        no_profit = client.post(
            "/api/tax/accrual-manual-request",
            headers=headers,
            json={**_accrual_body(setup), "cumulative_profit_cents": None},
        )
        assert no_profit.status_code == 409, no_profit.text
        assert "累计利润" in no_profit.json()["detail"]


def test_stamp_duty_three_states_through_the_api(tmp_path):
    """接口层必须区分「未给合同」与「确认零业务」，不能都回一个 0 元。"""
    app = create_app(tmp_path / "tax-stamp-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45695)) as client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        setup = _bound_book(app)
        book = setup["book"]
        bound = client.post(
            "/api/posting/opening-bindings",
            headers=headers,
            json={
                "book_id": book["id"],
                "package_version_id": setup["version_id"],
                "reason": "绑定印花税三态 HTTP 期初",
            },
        )
        assert bound.status_code == 201, bound.text
        reviewer, reviewer_headers = _firm_identity(app, "reviewer", "stamp-reviewer")
        app.state.directory.grant_book_member(
            LOCAL_ACTOR, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
        )
        _reviewed_workpaper(client, app, setup, headers, reviewer_headers)

        # /four-tax 不像计提接口那样回落到税务档案，附加税条目要显式给出。
        four_tax_body = {
            **_accrual_body(setup),
            "surtax_items": [{"code": "city", "rate_bp": 1200}],
        }
        missing = client.post("/api/tax/four-tax", headers=headers, json=four_tax_body)
        assert missing.status_code == 200, missing.text
        stamp = missing.json()["stamp_duty"]
        assert stamp["status"] == "unprovided"
        assert stamp["total_cents"] is None
        assert "不能视作本期零业务" in stamp["notice"]

        zero = client.post(
            "/api/tax/four-tax",
            headers=headers,
            json={**four_tax_body, "stamp_zero_confirmed": True},
        )
        assert zero.status_code == 200, zero.text
        assert zero.json()["stamp_duty"]["status"] == "zero_confirmed"
        assert zero.json()["stamp_duty"]["total_cents"] == 0

        conflict = client.post(
            "/api/tax/four-tax",
            headers=headers,
            json={
                **four_tax_body,
                "stamp_zero_confirmed": True,
                "stamp_contracts": [{"key": "c-1", "kind": "purchase", "cents": 1_000_000}],
                "stamp_rates": {"purchase": 30},
            },
        )
        assert conflict.status_code == 422, conflict.text
        assert "零业务" in conflict.json()["detail"]

        preview = client.post(
            "/api/tax/accrual-preview",
            headers=headers,
            json={**_accrual_body(setup), "stamp_zero_confirmed": True},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["stamp_duty_status"] == "zero_confirmed"
        assert "已确认本期无应税合同" in preview.json()["stamp_duty_notice"]
