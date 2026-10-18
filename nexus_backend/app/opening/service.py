"""B02: versioned accounting policy, stable ledger accounts and opening packages.

This module deliberately stops before posting. An approved/frozen opening package
is evidence for B03, not a journal, voucher number, ledger switch or report.
"""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import signed_money_to_cents
from ..accounting.service import now


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class OpeningService:
    def __init__(self, db):
        self.db = db

    # ---------- scope ----------

    def _ledger(self, conn, actor, ledger_id, manage=True):
        row = conn.execute(
            "SELECT l.*,fc.status AS client_status FROM ledgers l "
            "JOIN firm_clients fc ON fc.firm_id=l.firm_id AND fc.company_id=l.company_id "
            "WHERE l.id=? AND l.firm_id=?",
            (ledger_id, actor.firm_id),
        ).fetchone()
        if not row or row["client_status"] != "active":
            raise AccountingError("长期账套不存在、已停用或无权访问", 404)
        if manage:
            manager = (
                conn.execute(
                    "SELECT 1 FROM memberships WHERE firm_id=? AND user_id=? "
                    "AND role IN ('owner','admin')",
                    (actor.firm_id, actor.user_id),
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                    (ledger_id, actor.user_id),
                ).fetchone()
            )
            if not manager:
                raise AccountingError("需要事务所管理层或长期账套管理授权", 403)
        return row

    def _package(self, conn, actor, package_id):
        row = conn.execute(
            "SELECT p.*,l.name AS ledger_name FROM opening_packages p "
            "JOIN ledgers l ON l.id=p.ledger_id AND l.firm_id=p.firm_id "
            "WHERE p.id=? AND p.firm_id=?",
            (package_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("期初包不存在或无权访问", 404)
        self._ledger(conn, actor, row["ledger_id"], manage=True)
        return row

    # ---------- policy ----------

    def create_policy(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            ledger = self._ledger(conn, actor, request.ledger_id, manage=True)
            if request.effective_to and request.effective_to < request.effective_from:
                raise AccountingError("政策结束期间不能早于开始期间", 422)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM accounting_policies WHERE ledger_id=?",
                (ledger["id"],),
            ).fetchone()[0]
            conn.execute(
                "UPDATE accounting_policies SET status='retired' WHERE ledger_id=? AND status='active'",
                (ledger["id"],),
            )
            policy_id = uuid4().hex
            stamp = now()
            conn.execute(
                "INSERT INTO accounting_policies(id,firm_id,company_id,ledger_id,standard_kind,"
                "taxpayer_kind,industry,fiscal_year_start_month,currency,version,status,"
                "effective_from,effective_to,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,'CNY',?,'active',?,?,?,?,?)",
                (
                    policy_id,
                    ledger["firm_id"],
                    ledger["company_id"],
                    ledger["id"],
                    request.standard_kind,
                    request.taxpayer_kind,
                    request.industry,
                    request.fiscal_year_start_month,
                    version,
                    request.effective_from,
                    request.effective_to,
                    request.reason,
                    actor.user_id,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                ledger["id"],
                "opening.policy.create",
                policy_id,
                {
                    "standard_kind": request.standard_kind,
                    "taxpayer_kind": request.taxpayer_kind,
                    "version": version,
                },
            )
            return self._policy_row(conn, policy_id)

    @staticmethod
    def _policy_row(conn, policy_id):
        row = conn.execute("SELECT * FROM accounting_policies WHERE id=?", (policy_id,)).fetchone()
        return dict(row)

    def list_policies(self, actor, ledger_id):
        with self.db.transaction(actor=actor) as conn:
            self._ledger(conn, actor, ledger_id, manage=True)
            rows = conn.execute(
                "SELECT * FROM accounting_policies WHERE ledger_id=? ORDER BY version DESC",
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ---------- stable accounts ----------

    def _account(self, conn, ledger_id, account_id, version=None):
        # Stable account IDs and append-only version IDs are different keys.
        # Explicit labels avoid SQLite/PG duplicate-column lookup differences.
        fields = (
            "a.id,a.current_version,v.id AS version_id,v.account_id,v.ledger_id,v.firm_id,"
            "v.company_id,v.version,v.code,v.name,v.parent_account_id,v.level,v.is_leaf,"
            "v.category,v.normal_balance,v.auxiliary_requirements,v.effective_from,"
            "v.effective_to,v.status,v.reason,v.created_by,v.created_at "
        )
        if version is None:
            row = conn.execute(
                "SELECT " + fields + "FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.id=? AND a.ledger_id=?",
                (account_id, ledger_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT " + fields + "FROM ledger_accounts a "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=? "
                "WHERE a.id=? AND a.ledger_id=?",
                (version, account_id, ledger_id),
            ).fetchone()
        if not row:
            raise AccountingError("长期科目不存在或不属于当前账套", 404)
        return row

    def _validate_account_shape(self, conn, ledger_id, request, account_id=None):
        parent = None
        level = 1
        if request.parent_account_id:
            if request.parent_account_id == account_id:
                raise AccountingError("科目不能以自身为父级", 422)
            parent = self._account(conn, ledger_id, request.parent_account_id)
            level = int(parent["level"]) + 1
            if level > 6:
                raise AccountingError("科目层级最多 6 级", 422)
            if account_id:
                cursor = request.parent_account_id
                seen = set()
                while cursor:
                    if cursor == account_id:
                        raise AccountingError("科目父级关系不能成环", 422)
                    if cursor in seen:
                        raise AccountingError("现有科目父级关系存在环", 409)
                    seen.add(cursor)
                    row = conn.execute(
                        "SELECT parent_account_id FROM ledger_account_versions "
                        "WHERE account_id=? AND version=(SELECT current_version FROM ledger_accounts WHERE id=?)",
                        (cursor, cursor),
                    ).fetchone()
                    cursor = row["parent_account_id"] if row else None
        conflict = conn.execute(
            "SELECT 1 FROM ledger_account_versions v JOIN ledger_accounts a ON a.id=v.account_id "
            "WHERE a.ledger_id=? AND v.code=? AND v.status IN ('draft','active') "
            "AND (CAST(? AS TEXT) IS NULL OR v.account_id<>?) LIMIT 1",
            (ledger_id, request.code, account_id, account_id),
        ).fetchone()
        if conflict:
            raise AccountingError("同一长期账套内科目编码已存在", 409)
        return parent, level

    def create_account(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            ledger = self._ledger(conn, actor, request.ledger_id, manage=True)
            parent, level = self._validate_account_shape(conn, ledger["id"], request)
            stamp = now()
            account_id = uuid4().hex
            conn.execute(
                "INSERT INTO ledger_accounts(id,firm_id,company_id,ledger_id,current_version,status,"
                "created_by,created_at) VALUES(?,?,?,?,1,'active',?,?)",
                (
                    account_id,
                    ledger["firm_id"],
                    ledger["company_id"],
                    ledger["id"],
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,"
                "code,name,parent_account_id,level,is_leaf,category,normal_balance,"
                "auxiliary_requirements,effective_from,effective_to,status,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    uuid4().hex,
                    account_id,
                    ledger["id"],
                    ledger["firm_id"],
                    ledger["company_id"],
                    request.code,
                    request.name,
                    request.parent_account_id,
                    level,
                    1 if request.is_leaf else 0,
                    request.category,
                    request.normal_balance,
                    json.dumps(request.auxiliary_requirements, ensure_ascii=False),
                    request.effective_from,
                    request.effective_to,
                    request.reason,
                    actor.user_id,
                    stamp,
                ),
            )
            if parent and parent["is_leaf"]:
                self._revise_parent_leaf(conn, actor, ledger["id"], parent, stamp)
            self._audit(
                conn,
                actor,
                ledger["id"],
                "opening.account.create",
                account_id,
                {
                    "code": request.code,
                    "parent_account_id": request.parent_account_id,
                },
            )
            return self._account(conn, ledger["id"], account_id)

    def _revise_parent_leaf(self, conn, actor, ledger_id, parent, stamp):
        parent_row = self._account(conn, ledger_id, parent["id"])
        version = int(parent_row["version"]) + 1
        conn.execute(
            "INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,"
            "code,name,parent_account_id,level,is_leaf,category,normal_balance,"
            "auxiliary_requirements,effective_from,effective_to,status,reason,created_by,created_at) "
            "SELECT ?,account_id,ledger_id,firm_id,company_id,?,code,name,parent_account_id,level,0,"
            "category,normal_balance,auxiliary_requirements,effective_from,effective_to,'active',?,?,? "
            "FROM ledger_account_versions WHERE account_id=? AND version=?",
            (
                uuid4().hex,
                version,
                "新增子科目后父级转为汇总科目",
                actor.user_id,
                stamp,
                parent["id"],
                parent_row["version"],
            ),
        )
        conn.execute(
            "UPDATE ledger_accounts SET current_version=? WHERE id=? AND ledger_id=?",
            (version, parent["id"], ledger_id),
        )

    def revise_account(self, actor, account_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            ledger = self._ledger(conn, actor, request.ledger_id, manage=True)
            current = self._account(conn, ledger["id"], account_id)
            if int(current["version"]) != request.expected_version:
                raise AccountingError("科目版本已变化，请刷新后重试", 409)
            _, level = self._validate_account_shape(
                conn, ledger["id"], request, account_id=account_id
            )
            version = request.expected_version + 1
            conn.execute(
                "INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,"
                "code,name,parent_account_id,level,is_leaf,category,normal_balance,"
                "auxiliary_requirements,effective_from,effective_to,status,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    uuid4().hex,
                    account_id,
                    ledger["id"],
                    ledger["firm_id"],
                    ledger["company_id"],
                    version,
                    request.code,
                    request.name,
                    request.parent_account_id,
                    level,
                    1 if request.is_leaf else 0,
                    request.category,
                    request.normal_balance,
                    json.dumps(request.auxiliary_requirements, ensure_ascii=False),
                    request.effective_from,
                    request.effective_to,
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            conn.execute(
                "UPDATE ledger_accounts SET current_version=? WHERE id=? AND ledger_id=?",
                (version, account_id, ledger["id"]),
            )
            self._audit(
                conn,
                actor,
                ledger["id"],
                "opening.account.revise",
                account_id,
                {
                    "version": version,
                    "code": request.code,
                },
            )
            return self._account(conn, ledger["id"], account_id)

    def list_accounts(self, actor, ledger_id):
        with self.db.transaction(actor=actor) as conn:
            self._ledger(conn, actor, ledger_id, manage=True)
            rows = conn.execute(
                "SELECT a.id,a.status,a.current_version, v.code,v.name,v.parent_account_id,v.level,"
                "v.is_leaf,v.category,v.normal_balance,v.auxiliary_requirements,v.effective_from,"
                "v.effective_to FROM ledger_accounts a JOIN ledger_account_versions v "
                "ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE a.ledger_id=? ORDER BY v.code",
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_mapping(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = conn.execute(
                "SELECT b.*,lp.ledger_id FROM books b JOIN ledger_periods lp ON lp.book_id=b.id "
                "WHERE b.id=? AND b.firm_id=?",
                (request.book_id, actor.firm_id),
            ).fetchone()
            if not book:
                raise AccountingError("旧账套不存在或未关联长期账套", 404)
            self._ledger(conn, actor, book["ledger_id"], manage=True)
            account = self._account(
                conn, book["ledger_id"], request.account_id, request.account_version
            )
            exists = conn.execute(
                "SELECT 1 FROM accounts WHERE book_id=? AND code=?",
                (book["id"], request.book_account_code),
            ).fetchone()
            if not exists:
                raise AccountingError("旧账套科目不存在", 404)
            prior = conn.execute(
                "SELECT * FROM ledger_account_mappings WHERE book_id=? AND book_account_code=?",
                (book["id"], request.book_account_code),
            ).fetchone()
            if prior and (
                prior["account_id"] != request.account_id
                or int(prior["account_version"]) != request.account_version
            ):
                self._audit(
                    conn,
                    actor,
                    book["ledger_id"],
                    "opening.mapping.replace",
                    book["id"],
                    {
                        "code": request.book_account_code,
                        "before": dict(prior),
                        "after": {
                            "account_id": request.account_id,
                            "version": request.account_version,
                        },
                    },
                )
            conn.execute(
                "INSERT INTO ledger_account_mappings(book_id,firm_id,company_id,ledger_id,"
                "book_account_code,account_id,account_version,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(book_id,book_account_code) DO UPDATE SET "
                "account_id=excluded.account_id,account_version=excluded.account_version,"
                "created_by=excluded.created_by,created_at=excluded.created_at",
                (
                    book["id"],
                    book["firm_id"],
                    book["company_id"],
                    book["ledger_id"],
                    request.book_account_code,
                    request.account_id,
                    request.account_version,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book["ledger_id"],
                "opening.mapping.upsert",
                book["id"],
                {
                    "code": request.book_account_code,
                    "account_id": request.account_id,
                    "version": request.account_version,
                },
            )
            return {
                "book_id": book["id"],
                "book_account_code": request.book_account_code,
                "account_id": account["id"],
                "account_version": request.account_version,
            }

    def list_mappings(self, actor, ledger_id, book_id=None):
        with self.db.transaction(actor=actor) as conn:
            self._ledger(conn, actor, ledger_id, manage=True)
            if book_id:
                rows = conn.execute(
                    "SELECT * FROM ledger_account_mappings WHERE ledger_id=? AND book_id=? ORDER BY book_account_code",
                    (ledger_id, book_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ledger_account_mappings WHERE ledger_id=? ORDER BY book_id,book_account_code",
                    (ledger_id,),
                ).fetchall()
            return [dict(row) for row in rows]

    # ---------- opening package ----------

    def create_package(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            ledger = self._ledger(conn, actor, request.ledger_id, manage=True)
            policy = conn.execute(
                "SELECT * FROM accounting_policies WHERE id=? AND ledger_id=? AND status='active'",
                (request.policy_id, ledger["id"]),
            ).fetchone()
            if not policy:
                raise AccountingError("期初包必须绑定当前启用的核算政策", 409)
            if request.target_period < policy["effective_from"] or (
                policy["effective_to"] and request.target_period > policy["effective_to"]
            ):
                raise AccountingError("目标期间不在核算政策有效期内", 422)
            if conn.execute(
                "SELECT 1 FROM opening_packages WHERE ledger_id=? AND target_period=?",
                (ledger["id"], request.target_period),
            ).fetchone():
                raise AccountingError("该长期账套和期间已存在期初包", 409)
            package_id, stamp = uuid4().hex, now()
            conn.execute(
                "INSERT INTO opening_packages(id,firm_id,company_id,ledger_id,target_period,policy_id,"
                "status,current_version,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'draft',1,?,?,?)",
                (
                    package_id,
                    ledger["firm_id"],
                    ledger["company_id"],
                    ledger["id"],
                    request.target_period,
                    policy["id"],
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                ledger["id"],
                "opening.package.create",
                package_id,
                {
                    "target_period": request.target_period,
                    "policy_id": policy["id"],
                },
            )
            return self._package_view(conn, actor, package_id)

    def import_version(self, actor, package_id, request):
        digest = _fingerprint(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            package = self._package(conn, actor, package_id)
            prior = conn.execute(
                "SELECT * FROM opening_package_versions WHERE package_id=? AND request_key=?",
                (package_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("期初导入幂等键已用于不同内容", 409)
                return self._version_view(conn, actor, prior["id"], replayed=True)
            policy = conn.execute(
                "SELECT * FROM accounting_policies WHERE id=? AND ledger_id=? AND status='active'",
                (package["policy_id"], package["ledger_id"]),
            ).fetchone()
            if not policy:
                raise AccountingError("核算政策已停用或变更，请重新选择", 409)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM opening_package_versions WHERE package_id=?",
                (package_id,),
            ).fetchone()[0]
            version_id = uuid4().hex
            stamp = now()
            fingerprint = self._config_fingerprint(policy, request.lines)
            conn.execute(
                "INSERT INTO opening_package_versions(id,package_id,version,policy_version,"
                "config_fingerprint,status,request_key,request_fingerprint,supersedes_id,created_by,"
                "created_at,reason) VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?)",
                (
                    version_id,
                    package_id,
                    version,
                    policy["version"],
                    fingerprint,
                    request.idempotency_key,
                    digest,
                    None,
                    actor.user_id,
                    stamp,
                    request.reason,
                ),
            )
            seen = set()
            for index, line in enumerate(request.lines, start=1):
                account = self._account(
                    conn, package["ledger_id"], line.account_id, line.account_version
                )
                if not account["is_leaf"]:
                    raise AccountingError(
                        f"科目 {account['code']} 是汇总科目，不能直接录入期初", 422
                    )
                year = signed_money_to_cents(line.year_opening)
                debit = signed_money_to_cents(line.ytd_debit)
                credit = signed_money_to_cents(line.ytd_credit)
                opening = signed_money_to_cents(line.opening)
                if debit < 0 or credit < 0:
                    raise AccountingError("本年借贷累计不能为负数", 422)
                if year + debit - credit != opening:
                    raise AccountingError(
                        f"科目 {account['code']} 的年初+本期发生额不等于启用期期初", 422
                    )
                aux = json.dumps(line.auxiliary_values, ensure_ascii=False, sort_keys=True)
                duplicate = (line.account_id, aux)
                if duplicate in seen:
                    raise AccountingError("同一科目及辅助维度不能重复录入", 422)
                seen.add(duplicate)
                conn.execute(
                    "INSERT INTO opening_lines(id,package_version_id,line_no,account_id,"
                    "account_version,aux_json,year_opening_cents,ytd_debit_cents,ytd_credit_cents,"
                    "opening_cents,source_row_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        version_id,
                        index,
                        line.account_id,
                        line.account_version,
                        aux,
                        year,
                        debit,
                        credit,
                        opening,
                        json.dumps(line.source_row, ensure_ascii=False),
                    ),
                )
            for item in request.open_items:
                counterparty = conn.execute(
                    "SELECT 1 FROM company_master_entities WHERE id=? AND firm_id=? "
                    "AND company_id=? AND kind='counterparty' AND active=1",
                    (item.counterparty_id, package["firm_id"], package["company_id"]),
                ).fetchone()
                if not counterparty:
                    raise AccountingError("未结事项往来方不存在、停用或不属于本企业", 404)
                conn.execute(
                    "INSERT INTO opening_open_items(id,package_version_id,firm_id,company_id,kind,"
                    "counterparty_id,original_date,remaining_cents,source_ref) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        version_id,
                        package["firm_id"],
                        package["company_id"],
                        item.kind,
                        item.counterparty_id,
                        item.original_date,
                        signed_money_to_cents(item.remaining),
                        item.source_ref,
                    ),
                )
            conn.execute(
                "UPDATE opening_packages SET current_version=?,status='draft',updated_at=? WHERE id=?",
                (version, stamp, package_id),
            )
            self._audit(
                conn,
                actor,
                package["ledger_id"],
                "opening.version.import",
                version_id,
                {
                    "package_id": package_id,
                    "version": version,
                    "lines": len(request.lines),
                },
            )
            return self._version_view(conn, actor, version_id, replayed=False)

    @staticmethod
    def _config_fingerprint(policy, lines):
        return _fingerprint(
            {
                "policy_id": policy["id"],
                "policy_version": policy["version"],
                "accounts": sorted({f"{line.account_id}:{line.account_version}" for line in lines}),
            }
        )

    @staticmethod
    def _config_fingerprint_rows(policy, rows):
        return _fingerprint(
            {
                "policy_id": policy["id"],
                "policy_version": policy["version"],
                "accounts": sorted(
                    {f"{row['account_id']}:{row['account_version']}" for row in rows}
                ),
            }
        )

    def validate_version(self, actor, version_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            version = self._version(conn, actor, version_id)
            if version["status"] not in {"draft", "validated", "rejected"}:
                raise AccountingError("当前版本状态不能重新核验", 409)
            lines = self._lines(conn, version_id)
            checks = self._checks(conn, version)
            for code, status, details in checks:
                conn.execute(
                    "INSERT INTO opening_checks(id,package_version_id,check_code,status,"
                    "details_json,checked_at) VALUES(?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        version_id,
                        code,
                        status,
                        json.dumps(details, ensure_ascii=False),
                        now(),
                    ),
                )
            passed = all(status == "pass" for _, status, _ in checks)
            status = "validated" if passed else "draft"
            conn.execute(
                "UPDATE opening_package_versions SET status=? WHERE id=?", (status, version_id)
            )
            conn.execute(
                "UPDATE opening_packages SET status=?,updated_at=? WHERE id=?",
                (status, now(), version["package_id"]),
            )
            return self._version_view(conn, actor, version_id, replayed=False)

    def _checks(self, conn, version):
        lines = self._lines(conn, version["id"])
        checks = []
        total = sum(int(row["opening_cents"]) for row in lines)
        checks.append(("balance", "pass" if total == 0 else "fail", {"opening_total": total}))
        math_fail = []
        for row in lines:
            expected = (
                int(row["year_opening_cents"])
                + int(row["ytd_debit_cents"])
                - int(row["ytd_credit_cents"])
            )
            if expected != int(row["opening_cents"]):
                math_fail.append(row["account_id"])
        checks.append(("line_math", "pass" if not math_fail else "fail", {"accounts": math_fail}))
        stale = []
        for row in lines:
            account = conn.execute(
                "SELECT current_version FROM ledger_accounts WHERE id=? AND ledger_id=?",
                (row["account_id"], version["ledger_id"]),
            ).fetchone()
            if not account or int(account["current_version"]) != int(row["account_version"]):
                stale.append(row["account_id"])
        policy = conn.execute(
            "SELECT id,version FROM accounting_policies WHERE id=? AND ledger_id=? AND status='active'",
            (version["policy_id"], version["ledger_id"]),
        ).fetchone()
        if not policy or int(policy["version"]) != int(version["policy_version"]):
            stale.append("policy")
        checks.append(("config_current", "pass" if not stale else "blocked", {"stale": stale}))
        checks.append(
            (
                "duplicate_lines",
                "pass"
                if len(lines) == len({(row["account_id"], row["aux_json"]) for row in lines})
                else "fail",
                {},
            )
        )
        if policy:
            current_fingerprint = self._config_fingerprint_rows(policy, lines)
            checks.append(
                (
                    "config_fingerprint",
                    "pass" if current_fingerprint == version["config_fingerprint"] else "blocked",
                    {},
                )
            )
        else:
            checks.append(("config_fingerprint", "blocked", {}))
        return checks

    def _assert_current(self, conn, version):
        failed = [code for code, status, _ in self._checks(conn, version) if status != "pass"]
        if failed:
            raise AccountingError("核验未通过或配置已变化：" + "、".join(failed), 409)

    def submit_version(self, actor, version_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            version = self._version(conn, actor, version_id)
            if version["status"] != "validated":
                raise AccountingError("只有核验通过的期初版本才能提交", 409)
            if int(version["version"]) != request.expected_version:
                raise AccountingError("期初版本已变化，请刷新后重试", 409)
            self._assert_current(conn, version)
            conn.execute(
                "UPDATE opening_package_versions SET status='submitted',submitted_by=?,"
                "submitted_at=?,reason=? WHERE id=? AND status='validated'",
                (actor.user_id, now(), request.reason, version_id),
            )
            conn.execute(
                "UPDATE opening_packages SET status='submitted',updated_at=? WHERE id=?",
                (now(), version["package_id"]),
            )
            self._audit(
                conn,
                actor,
                version["ledger_id"],
                "opening.version.submit",
                version_id,
                {
                    "version": version["version"],
                },
            )
            return self._version_view(conn, actor, version_id, replayed=False)

    def approve_version(self, actor, version_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            version = self._version(conn, actor, version_id)
            if version["status"] != "submitted":
                raise AccountingError("只有已提交的期初版本才能核准", 409)
            if int(version["version"]) != request.expected_version:
                raise AccountingError("期初版本已变化，请刷新后重试", 409)
            if version["submitted_by"] == actor.user_id:
                raise AccountingError("期初提交人与核准人必须分离（待会计负责人确认）", 403)
            self._assert_current(conn, version)
            conn.execute(
                "UPDATE opening_package_versions SET status='superseded' "
                "WHERE package_id=? AND status IN ('approved','frozen') AND id<>?",
                (version["package_id"], version_id),
            )
            conn.execute(
                "UPDATE opening_package_versions SET status='approved',approved_by=?,approved_at=?,"
                "reason=? WHERE id=? AND status='submitted'",
                (actor.user_id, now(), request.reason, version_id),
            )
            conn.execute(
                "UPDATE opening_packages SET status='approved',updated_at=? WHERE id=?",
                (now(), version["package_id"]),
            )
            self._audit(
                conn,
                actor,
                version["ledger_id"],
                "opening.version.approve",
                version_id,
                {
                    "version": version["version"],
                    "approved_by": actor.user_id,
                },
            )
            return self._version_view(conn, actor, version_id, replayed=False)

    def freeze_version(self, actor, version_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            version = self._version(conn, actor, version_id)
            if version["status"] != "approved":
                raise AccountingError("只有已核准的期初版本才能冻结", 409)
            if int(version["version"]) != request.expected_version:
                raise AccountingError("期初版本已变化，请刷新后重试", 409)
            conn.execute(
                "UPDATE opening_package_versions SET status='frozen',frozen_at=?,reason=? "
                "WHERE id=? AND status='approved'",
                (now(), request.reason, version_id),
            )
            conn.execute(
                "UPDATE opening_packages SET status='frozen',updated_at=? WHERE id=?",
                (now(), version["package_id"]),
            )
            self._audit(
                conn,
                actor,
                version["ledger_id"],
                "opening.version.freeze",
                version_id,
                {
                    "version": version["version"],
                },
            )
            return self._version_view(conn, actor, version_id, replayed=False)

    def _version(self, conn, actor, version_id):
        row = conn.execute(
            "SELECT v.*,p.ledger_id,p.policy_id,p.firm_id,p.company_id,p.target_period "
            "FROM opening_package_versions v JOIN opening_packages p ON p.id=v.package_id "
            "WHERE v.id=? AND p.firm_id=?",
            (version_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("期初版本不存在或无权访问", 404)
        self._ledger(conn, actor, row["ledger_id"], manage=True)
        return row

    @staticmethod
    def _lines(conn, version_id):
        return conn.execute(
            "SELECT * FROM opening_lines WHERE package_version_id=? ORDER BY line_no",
            (version_id,),
        ).fetchall()

    @staticmethod
    def _latest_checks(conn, version_id):
        return conn.execute(
            "SELECT * FROM opening_checks WHERE package_version_id=? ORDER BY checked_at DESC,id DESC",
            (version_id,),
        ).fetchall()

    def list_packages(self, actor, ledger_id):
        with self.db.transaction(actor=actor) as conn:
            self._ledger(conn, actor, ledger_id, manage=True)
            rows = conn.execute(
                "SELECT * FROM opening_packages WHERE ledger_id=? ORDER BY target_period DESC",
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def _package_view(self, conn, actor, package_id):
        package = self._package(conn, actor, package_id)
        versions = conn.execute(
            "SELECT * FROM opening_package_versions WHERE package_id=? ORDER BY version DESC",
            (package_id,),
        ).fetchall()
        return {
            "package": dict(package),
            "versions": [dict(row) for row in versions],
        }

    def _version_view(self, conn, actor, version_id, replayed=False):
        version = self._version(conn, actor, version_id)
        open_items = conn.execute(
            "SELECT * FROM opening_open_items WHERE package_version_id=? ORDER BY kind,original_date,id",
            (version_id,),
        ).fetchall()
        return {
            "version": dict(version),
            "lines": [dict(row) for row in self._lines(conn, version_id)],
            "open_items": [dict(row) for row in open_items],
            "checks": [dict(row) for row in self._latest_checks(conn, version_id)],
            "replayed": replayed,
            "posting": None,
            "note": "核准/冻结只表示期初准备包通过，不是正式记账或账本启用",
        }

    @staticmethod
    def _audit(conn, actor, ledger_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,NULL,?,?,?,?,?)",
            (
                actor.firm_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
