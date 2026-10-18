"""Deterministic B03 posting command and ledger read models.

Formal vouchers are append-only. Draft review, internal posting and external
delivery remain three separate state axes; this module never changes
drafts.status=exported or triggers delivery.
"""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents
from ..accounting.service import AccountingService, REVIEW_POLICY_VERSION
from .models import ManualVoucher, OpeningBind, PostDraft, ReverseVoucher


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PostingService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    # ---------- scope and opening ----------

    def _book(self, conn, actor, book_id, write=False):
        book = self.accounting.access.book(conn, actor, book_id, "review" if write else "read")
        if write and book["period_status"] != "open":
            raise AccountingError("当前会计期间已关闭，不能正式记账", 403)
        if (
            write
            and conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
                (book["id"], book["period"]),
            ).fetchone()
        ):
            raise AccountingError("当前会计期间已内部锁期，不能正式记账", 403)
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()
        if not ledger:
            raise AccountingError("账套尚未关联长期账套，不能正式记账", 409)
        return book, ledger["ledger_id"]

    def _binding(self, conn, book_id):
        row = conn.execute(
            "SELECT b.*,v.config_fingerprint AS package_fingerprint,v.status AS version_status "
            "FROM opening_bindings b JOIN opening_package_versions v ON v.id=b.package_version_id "
            "WHERE b.book_id=? AND b.status='active'",
            (book_id,),
        ).fetchone()
        if not row:
            raise AccountingError("账套尚未绑定冻结期初，不能正式记账", 409)
        if row["version_status"] != "frozen":
            raise AccountingError("绑定期初不是冻结版本，不能正式记账", 409)
        if row["snapshot_fingerprint"] != row["package_fingerprint"]:
            raise AccountingError("期初绑定指纹已变化，不能正式记账", 409)
        return row

    def bind_opening(self, actor, request: OpeningBind):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            version = conn.execute(
                "SELECT v.*,p.ledger_id,p.target_period FROM opening_package_versions v "
                "JOIN opening_packages p ON p.id=v.package_id "
                "WHERE v.id=? AND p.firm_id=?",
                (request.package_version_id, actor.firm_id),
            ).fetchone()
            if not version:
                raise AccountingError("期初版本不存在或无权访问", 404)
            if version["ledger_id"] != ledger_id or version["target_period"] != book["period"]:
                raise AccountingError("期初包与账套/期间不一致", 409)
            if version["status"] != "frozen":
                raise AccountingError("只能绑定已冻结的期初准备包", 409)
            prior = conn.execute(
                "SELECT * FROM opening_bindings WHERE book_id=? AND status='active'",
                (book["id"],),
            ).fetchone()
            if prior:
                if prior["package_version_id"] == version["id"]:
                    return self._binding_view(prior, replayed=True)
                raise AccountingError("该账套已绑定其他期初版本；更正请走明确更正流程", 409)
            binding_id = uuid4().hex
            conn.execute(
                "INSERT INTO opening_bindings(id,firm_id,company_id,ledger_id,book_id,period,"
                "package_version_id,snapshot_fingerprint,status,bound_by,bound_at) "
                "VALUES(?,?,?,?,?,?,?,?,'active',?,?)",
                (
                    binding_id,
                    book["firm_id"],
                    book["company_id"],
                    ledger_id,
                    book["id"],
                    book["period"],
                    version["id"],
                    version["config_fingerprint"],
                    actor.user_id,
                    self._now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book["id"],
                "posting.opening.bind",
                binding_id,
                {
                    "package_version_id": version["id"],
                    "fingerprint": version["config_fingerprint"],
                },
            )
            return self._binding_view(
                conn.execute("SELECT * FROM opening_bindings WHERE id=?", (binding_id,)).fetchone(),
                replayed=False,
            )

    @staticmethod
    def _binding_view(row, replayed=False):
        return {
            "binding": dict(row),
            "replayed": replayed,
            "note": "期初绑定只供内部核算使用，不代表正式账本启用",
        }

    # ---------- number ----------

    def _next_number(self, conn, book_id, voucher_type):
        row = conn.execute(
            "SELECT next_no FROM voucher_sequences WHERE book_id=? AND voucher_type=?",
            (book_id, voucher_type),
        ).fetchone()
        if row:
            number = int(row["next_no"])
            conn.execute(
                "UPDATE voucher_sequences SET next_no=next_no+1 WHERE book_id=? AND voucher_type=?",
                (book_id, voucher_type),
            )
        else:
            number = 1
            conn.execute(
                "INSERT INTO voucher_sequences(book_id,voucher_type,next_no) VALUES(?,?,2)",
                (book_id, voucher_type),
            )
        return number

    # ---------- validation ----------

    def _validate_lines(self, conn, lines, allow_stale=False):
        if len(lines) < 2:
            raise AccountingError("凭证至少需要两条分录", 422)
        total_debit = total_credit = 0
        result = []
        for index, line in enumerate(lines, start=1):
            account = conn.execute(
                "SELECT a.status AS account_status,a.current_version,v.* "
                "FROM ledger_accounts a JOIN ledger_account_versions v "
                "ON v.account_id=a.id AND v.version=? WHERE a.id=?",
                (line["account_version"], line["account_id"]),
            ).fetchone()
            if not account:
                raise AccountingError("科目或版本不存在", 422)
            if account["account_status"] != "active" or account["status"] != "active":
                raise AccountingError("科目已停用", 422)
            if not allow_stale and int(account["current_version"]) != int(line["account_version"]):
                raise AccountingError("科目版本已变化，请重新选择", 409)
            if not account["is_leaf"]:
                raise AccountingError("汇总科目不能直接记账", 422)
            debit = money_to_cents(line["debit"])
            credit = money_to_cents(line["credit"])
            if (debit > 0) == (credit > 0):
                raise AccountingError("每条分录必须且只能有一侧金额", 422)
            aux = line.get("auxiliary_values") or {}
            required = json.loads(account["auxiliary_requirements"] or "[]")
            missing = [key for key in required if not str(aux.get(key, "")).strip()]
            if missing:
                raise AccountingError("缺少辅助核算：" + "、".join(missing), 422)
            total_debit += debit
            total_credit += credit
            result.append(
                {
                    "account_id": account["account_id"],
                    "account_version": account["version"],
                    "code_snapshot": account["code"],
                    "name_snapshot": account["name"],
                    "category_snapshot": account["category"],
                    "normal_balance_snapshot": account["normal_balance"],
                    "debit_cents": debit,
                    "credit_cents": credit,
                    "aux_json": json.dumps(aux, ensure_ascii=False, sort_keys=True),
                    "source_ref": line.get("source_ref", ""),
                }
            )
        if total_debit <= 0 or total_debit != total_credit:
            raise AccountingError("借贷不平衡或金额为零，不能记账", 422)
        return result, total_debit

    def _content_fingerprint(self, book_id, voucher_date, voucher_type, description, lines):
        return _fingerprint(
            {
                "book_id": book_id,
                "date": voucher_date,
                "type": voucher_type,
                "description": description,
                "lines": [
                    {
                        "account_id": line["account_id"],
                        "account_version": line["account_version"],
                        "debit": line["debit_cents"],
                        "credit": line["credit_cents"],
                        "aux": line["aux_json"],
                    }
                    for line in lines
                ],
            }
        )

    # ---------- atomic insertion ----------

    def _insert_voucher(
        self,
        conn,
        actor,
        book,
        binding,
        *,
        voucher_date,
        voucher_type,
        description,
        lines,
        source_kind,
        source_id=None,
        source_version=None,
        reversal_of=None,
        relation_kind=None,
        command_key,
        request_fingerprint,
        evidence_ref="",
    ):
        if voucher_date[:7] != book["period"]:
            raise AccountingError("记账日期必须属于当前账套期间", 422)
        number = self._next_number(conn, book["id"], voucher_type)
        voucher_id, command_id = uuid4().hex, uuid4().hex
        fingerprint = self._content_fingerprint(
            book["id"], voucher_date, voucher_type, description, lines
        )
        stamp = self._now()
        conn.execute(
            "INSERT INTO vouchers(id,firm_id,company_id,ledger_id,book_id,period,voucher_type,"
            "voucher_no,voucher_date,status,description,source_kind,source_id,source_version,"
            "opening_binding_id,review_policy_version,content_fingerprint,reversal_of,relation_kind,"
            "command_id,posted_by,posted_at) VALUES(?,?,?,?,?,?,?,?,?,'posted',?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                voucher_id,
                book["firm_id"],
                book["company_id"],
                binding["ledger_id"],
                book["id"],
                book["period"],
                voucher_type,
                number,
                voucher_date,
                description,
                source_kind,
                source_id,
                source_version,
                binding["id"],
                REVIEW_POLICY_VERSION,
                fingerprint,
                reversal_of,
                relation_kind,
                command_id,
                actor.user_id,
                stamp,
            ),
        )
        for index, line in enumerate(lines, start=1):
            conn.execute(
                "INSERT INTO voucher_lines(id,voucher_id,line_no,account_id,account_version,"
                "code_snapshot,name_snapshot,category_snapshot,normal_balance_snapshot,"
                "debit_cents,credit_cents,aux_json,source_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    voucher_id,
                    index,
                    line["account_id"],
                    line["account_version"],
                    line["code_snapshot"],
                    line["name_snapshot"],
                    line["category_snapshot"],
                    line["normal_balance_snapshot"],
                    line["debit_cents"],
                    line["credit_cents"],
                    line["aux_json"],
                    line["source_ref"],
                ),
            )
        if source_id and source_kind in {"draft", "source", "manual"}:
            conn.execute(
                "INSERT INTO voucher_sources(id,voucher_id,source_kind,source_id,source_version,"
                "amount_cents,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    voucher_id,
                    source_kind,
                    source_id,
                    source_version,
                    sum(line["debit_cents"] for line in lines),
                    evidence_ref,
                ),
            )
        conn.execute(
            "INSERT INTO posting_commands(id,firm_id,company_id,book_id,command_key,"
            "request_fingerprint,voucher_id,result_json,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,? ,?,?)",
            (
                command_id,
                book["firm_id"],
                book["company_id"],
                book["id"],
                command_key,
                request_fingerprint,
                voucher_id,
                json.dumps({"voucher_id": voucher_id, "voucher_no": number}, ensure_ascii=False),
                actor.user_id,
                stamp,
            ),
        )
        self._audit(
            conn,
            actor,
            book["id"],
            "posting.voucher.post",
            voucher_id,
            {
                "voucher_no": number,
                "voucher_type": voucher_type,
                "source_kind": source_kind,
                "source_id": source_id,
                "command_key": command_key,
            },
        )
        return self._voucher_view(conn, voucher_id)

    def _command_replay(self, conn, actor, book_id, command_key, request_fingerprint):
        row = conn.execute(
            "SELECT * FROM posting_commands WHERE firm_id=? AND book_id=? AND command_key=?",
            (actor.firm_id, book_id, command_key),
        ).fetchone()
        if not row:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise AccountingError("记账幂等键已用于不同内容", 409)
        return self._voucher_view(conn, row["voucher_id"], replayed=True)

    # ---------- commands ----------

    def post_draft(self, actor, book_id, draft_id, request: PostDraft):
        fingerprint = _fingerprint({"draft_id": draft_id, **request.model_dump()})
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id, write=True)
            replay = self._command_replay(conn, actor, book_id, request.command_key, fingerprint)
            if replay:
                return replay
            binding = self._binding(conn, book["id"])
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book["id"])
            ).fetchone()
            if not draft:
                raise AccountingError("草稿不存在或不属于当前账套", 404)
            if draft["status"] not in {"approved", "exported"}:
                raise AccountingError("只有已审核或已导出的草稿才能正式记账", 409)
            if int(draft["version"]) != request.expected_version:
                raise AccountingError("草稿版本已变化，请重新审核", 409)
            source = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?",
                (draft["source_id"], book["id"]),
            ).fetchone()
            if not source:
                raise AccountingError("草稿来源不存在", 409)
            if (
                conn.execute(
                    "SELECT 1 FROM bank_source_dispositions WHERE book_id=? AND source_id=?",
                    (book["id"], source["id"]),
                ).fetchone()
                or conn.execute(
                    "SELECT 1 FROM fact_revision_lineage WHERE old_book_id=? AND old_source_id=?",
                    (book["id"], source["id"]),
                ).fetchone()
            ):
                raise AccountingError("来源已隔离或已被替代，不能记账", 409)
            if conn.execute(
                "SELECT 1 FROM voucher_sources WHERE source_kind='draft' AND source_id=?",
                (draft_id,),
            ).fetchone():
                raise AccountingError("该草稿已经生成过正式凭证", 409)
            draft_lines = conn.execute(
                "SELECT * FROM draft_lines WHERE draft_id=? AND book_id=? ORDER BY ordinal",
                (draft_id, book["id"]),
            ).fetchall()
            lines = []
            for row in draft_lines:
                mapping = conn.execute(
                    "SELECT * FROM ledger_account_mappings WHERE book_id=? AND book_account_code=?",
                    (book["id"], row["account_code"]),
                ).fetchone()
                if not mapping:
                    raise AccountingError(
                        "旧账套科目尚未映射到长期稳定科目：" + row["account_code"], 409
                    )
                aux = conn.execute(
                    "SELECT values_json FROM draft_line_auxiliaries "
                    "WHERE draft_id=? AND book_id=? AND ordinal=?",
                    (draft_id, book["id"], row["ordinal"]),
                ).fetchone()
                lines.append(
                    {
                        "account_id": mapping["account_id"],
                        "account_version": mapping["account_version"],
                        "debit": self._cents(row["debit_cents"]),
                        "credit": self._cents(row["credit_cents"]),
                        "auxiliary_values": json.loads(aux["values_json"]) if aux else {},
                        "source_ref": source["id"],
                    }
                )
            validated, total = self._validate_lines(conn, lines)
            if total != int(source["amount_cents"]):
                raise AccountingError("凭证金额与来源金额不一致", 409)
            return self._insert_voucher(
                conn,
                actor,
                book,
                binding,
                voucher_date=request.voucher_date,
                voucher_type=request.voucher_type,
                description=request.description or draft["description"],
                lines=validated,
                source_kind="draft",
                source_id=draft_id,
                source_version=draft["version"],
                command_key=request.command_key,
                request_fingerprint=fingerprint,
                evidence_ref=source["evidence_ref"],
            )

    def post_manual(self, actor, request: ManualVoucher):
        fingerprint = _fingerprint(request.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._book(conn, actor, request.book_id, write=True)
            replay = self._command_replay(conn, actor, book["id"], request.command_key, fingerprint)
            if replay:
                return replay
            binding = self._binding(conn, book["id"])
            validated, _ = self._validate_lines(conn, [line.model_dump() for line in request.lines])
            original = None
            if request.relation_kind:
                if not request.original_voucher_id:
                    raise AccountingError("调整凭证必须关联原凭证", 422)
                original = conn.execute(
                    "SELECT * FROM vouchers WHERE id=? AND book_id=?",
                    (request.original_voucher_id, book["id"]),
                ).fetchone()
                if not original:
                    raise AccountingError("原凭证不存在或不属于当前账套", 404)
            view = self._insert_voucher(
                conn,
                actor,
                book,
                binding,
                voucher_date=request.voucher_date,
                voucher_type=request.voucher_type,
                description=request.description,
                lines=validated,
                source_kind="manual",
                command_key=request.command_key,
                request_fingerprint=fingerprint,
            )
            if original:
                conn.execute(
                    "INSERT INTO voucher_relations(id,original_voucher_id,related_voucher_id,"
                    "kind,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        original["id"],
                        view["voucher"]["id"],
                        "adjustment",
                        request.reason,
                        actor.user_id,
                        self._now(),
                    ),
                )
                return self._voucher_view(conn, view["voucher"]["id"])
            return view

    def reverse_voucher(self, actor, voucher_id, request: ReverseVoucher):
        fingerprint = _fingerprint({"voucher_id": voucher_id, **request.model_dump()})
        with self.db.transaction(write=True, actor=actor) as conn:
            original = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
            if not original:
                raise AccountingError("原凭证不存在", 404)
            book, _ = self._book(conn, actor, original["book_id"], write=True)
            replay = self._command_replay(conn, actor, book["id"], request.command_key, fingerprint)
            if replay:
                return replay
            binding = self._binding(conn, book["id"])
            if conn.execute(
                "SELECT 1 FROM voucher_relations WHERE original_voucher_id=? AND kind='reversal'",
                (voucher_id,),
            ).fetchone():
                raise AccountingError("该凭证已经全额冲销", 409)
            original_lines = conn.execute(
                "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no", (voucher_id,)
            ).fetchall()
            lines = [
                {
                    "account_id": row["account_id"],
                    "account_version": row["account_version"],
                    "code_snapshot": row["code_snapshot"],
                    "name_snapshot": row["name_snapshot"],
                    "category_snapshot": row["category_snapshot"],
                    "normal_balance_snapshot": row["normal_balance_snapshot"],
                    "debit_cents": row["credit_cents"],
                    "credit_cents": row["debit_cents"],
                    "aux_json": row["aux_json"],
                    "source_ref": row["source_ref"],
                }
                for row in original_lines
            ]
            if sum(row["debit_cents"] for row in lines) <= 0:
                raise AccountingError("原凭证没有可冲销金额", 409)
            view = self._insert_voucher(
                conn,
                actor,
                book,
                binding,
                voucher_date=request.voucher_date,
                voucher_type=original["voucher_type"],
                description="冲销 " + original["voucher_type"] + "-" + str(original["voucher_no"]),
                lines=lines,
                source_kind="reversal",
                source_id=original["id"],
                reversal_of=original["id"],
                relation_kind="reversal",
                command_key=request.command_key,
                request_fingerprint=fingerprint,
            )
            conn.execute(
                "INSERT INTO voucher_relations(id,original_voucher_id,related_voucher_id,kind,"
                "reason,created_by,created_at) VALUES(?,?,?,'reversal',?,?,?)",
                (
                    uuid4().hex,
                    original["id"],
                    view["voucher"]["id"],
                    request.reason,
                    actor.user_id,
                    self._now(),
                ),
            )
            return self._voucher_view(conn, view["voucher"]["id"])

    # ---------- read models ----------

    def list_vouchers(self, actor, book_id, limit=50, offset=0):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM vouchers WHERE book_id=? ORDER BY voucher_date DESC,"
                "voucher_type,voucher_no DESC LIMIT ? OFFSET ?",
                (book["id"], limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def lookup_command(self, actor, book_id, command_key):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM posting_commands WHERE firm_id=? AND book_id=? AND command_key=?",
                (actor.firm_id, book["id"], command_key),
            ).fetchone()
            if not row:
                raise AccountingError("记账命令不存在", 404)
            return self._voucher_view(conn, row["voucher_id"], replayed=True)

    def voucher_detail(self, actor, voucher_id):
        with self.db.transaction(actor=actor) as conn:
            voucher = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
            if not voucher:
                raise AccountingError("凭证不存在", 404)
            self._book(conn, actor, voucher["book_id"])
            return self._voucher_view(conn, voucher_id)

    def trial_balance(self, actor, book_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            binding = self._binding(conn, book["id"])
            opening = conn.execute(
                "SELECT * FROM opening_lines WHERE package_version_id=?",
                (binding["package_version_id"],),
            ).fetchall()
            voucher_rows = conn.execute(
                "SELECT l.*,v.voucher_date,v.voucher_no,v.voucher_type FROM voucher_lines l "
                "JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? "
                + ("AND v.voucher_date<=? " if as_of else "")
                + "ORDER BY v.voucher_date,v.voucher_no,l.line_no",
                (book["id"], as_of) if as_of else (book["id"],),
            ).fetchall()
            return self._trial_balance_rows(conn, binding, opening, voucher_rows)

    def _trial_balance_rows(self, conn, binding, opening, voucher_rows):
        accounts = {
            row["id"]: dict(row)
            for row in conn.execute(
                "SELECT a.id,v.code,v.name,v.parent_account_id,v.level,v.is_leaf,"
                "v.category,v.normal_balance FROM ledger_accounts a JOIN ledger_account_versions v "
                "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=?",
                (binding["ledger_id"],),
            )
        }
        own = {account_id: {"opening": 0, "debit": 0, "credit": 0} for account_id in accounts}
        for row in opening:
            if row["account_id"] in own:
                own[row["account_id"]]["opening"] += int(row["opening_cents"])
        for row in voucher_rows:
            if row["account_id"] in own:
                own[row["account_id"]]["debit"] += int(row["debit_cents"])
                own[row["account_id"]]["credit"] += int(row["credit_cents"])
        children = {}
        for account_id, meta in accounts.items():
            parent = meta["parent_account_id"]
            if parent:
                children.setdefault(parent, []).append(account_id)
        totals = {}

        def total(account_id, seen):
            if account_id in totals:
                return totals[account_id]
            if account_id in seen:
                raise AccountingError("科目层级存在环，不能生成余额", 409)
            seen.add(account_id)
            values = dict(own[account_id])
            for child in children.get(account_id, []):
                child_values = total(child, seen)
                values["opening"] += child_values["opening"]
                values["debit"] += child_values["debit"]
                values["credit"] += child_values["credit"]
            totals[account_id] = values
            return values

        rows = []
        for account_id, meta in sorted(accounts.items(), key=lambda item: item[1]["code"]):
            values = total(account_id, set())
            closing = values["opening"] + values["debit"] - values["credit"]
            if not values["debit"] and not values["credit"] and not values["opening"]:
                continue
            rows.append(
                {
                    "account_id": account_id,
                    "code": meta["code"],
                    "name": meta["name"],
                    "parent_account_id": meta["parent_account_id"],
                    "is_leaf": bool(meta["is_leaf"]),
                    "category": meta["category"],
                    "normal_balance": meta["normal_balance"],
                    "opening_cents": values["opening"],
                    "debit_cents": values["debit"],
                    "credit_cents": values["credit"],
                    "closing_cents": closing,
                    "direction": "debit" if closing >= 0 else "credit",
                }
            )
        leaf_rows = [row for row in rows if row["is_leaf"]]
        totals_out = {
            key: sum(row[key] for row in leaf_rows)
            for key in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
        }
        return {"rows": rows, "totals": totals_out}

    def account_ledger(self, actor, book_id, account_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            binding = self._binding(conn, book["id"])
            opening = conn.execute(
                "SELECT COALESCE(SUM(opening_cents),0) AS total FROM opening_lines "
                "WHERE package_version_id=? AND account_id=?",
                (binding["package_version_id"], account_id),
            ).fetchone()["total"]
            rows = conn.execute(
                "SELECT v.voucher_date,v.voucher_no,v.voucher_type,v.description,"
                "l.debit_cents,l.credit_cents,l.aux_json,l.line_no FROM voucher_lines l "
                "JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? AND l.account_id=? "
                + ("AND v.voucher_date<=? " if as_of else "")
                + "ORDER BY v.voucher_date,v.voucher_type,v.voucher_no,l.line_no",
                (book["id"], account_id, as_of) if as_of else (book["id"], account_id),
            ).fetchall()
            running = int(opening)
            entries = []
            for row in rows:
                running += int(row["debit_cents"]) - int(row["credit_cents"])
                entries.append({**dict(row), "balance_cents": running})
            return {"opening_cents": int(opening), "entries": entries, "closing_cents": running}

    def daily_journal(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            binding = self._binding(conn, book["id"])
            rows = conn.execute(
                "SELECT v.voucher_date,v.voucher_no,v.voucher_type,v.description,"
                "l.account_id,l.code_snapshot,l.name_snapshot,l.debit_cents,l.credit_cents,"
                "l.aux_json,l.line_no FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                "WHERE v.book_id=? AND (l.code_snapshot LIKE '1001%' OR l.code_snapshot LIKE '1002%') "
                "ORDER BY v.voucher_date,v.voucher_type,v.voucher_no,l.line_no",
                (book["id"],),
            ).fetchall()
            opening = {
                row["account_id"]: int(row["total"])
                for row in conn.execute(
                    "SELECT ol.account_id,COALESCE(SUM(ol.opening_cents),0) AS total "
                    "FROM opening_lines ol JOIN ledger_account_versions av "
                    "ON av.account_id=ol.account_id AND av.version=ol.account_version "
                    "WHERE ol.package_version_id=? AND (av.code LIKE '1001%' OR av.code LIKE '1002%') "
                    "GROUP BY ol.account_id",
                    (binding["package_version_id"],),
                )
            }
            running = dict(opening)
            result = []
            for row in rows:
                running[row["account_id"]] = (
                    running.get(row["account_id"], 0)
                    + int(row["debit_cents"])
                    - int(row["credit_cents"])
                )
                result.append({**dict(row), "balance_cents": running[row["account_id"]]})
            return result

    def aux_ledger(self, actor, book_id, auxiliary_values):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            binding = self._binding(conn, book["id"])
            wanted = {key: str(value) for key, value in auxiliary_values.items()}
            opening = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM opening_lines WHERE package_version_id=?",
                    (binding["package_version_id"],),
                )
                if self._aux_matches(row["aux_json"], wanted)
            ]
            vouchers = [
                dict(row)
                for row in conn.execute(
                    "SELECT l.*,v.voucher_date,v.voucher_no,v.voucher_type,v.description "
                    "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=?",
                    (book["id"],),
                )
                if self._aux_matches(row["aux_json"], wanted)
            ]
            return {"opening": opening, "vouchers": vouchers}

    @staticmethod
    def _aux_matches(value, wanted):
        try:
            current = json.loads(value or "{}")
        except (TypeError, ValueError):
            return False
        return all(str(current.get(key, "")) == expected for key, expected in wanted.items())

    def _voucher_view(self, conn, voucher_id, replayed=False):
        voucher = conn.execute("SELECT * FROM vouchers WHERE id=?", (voucher_id,)).fetchone()
        if not voucher:
            raise AccountingError("正式凭证不存在", 404)
        lines = conn.execute(
            "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no", (voucher_id,)
        ).fetchall()
        relations = conn.execute(
            "SELECT * FROM voucher_relations WHERE original_voucher_id=? OR related_voucher_id=?",
            (voucher_id, voucher_id),
        ).fetchall()
        return {
            "voucher": dict(voucher),
            "lines": [dict(row) for row in lines],
            "relations": [dict(row) for row in relations],
            "replayed": replayed,
            "posting": "posted",
            "note": "内部记账完成，不代表外部财务软件或税务机关已入账",
        }

    @staticmethod
    def _cents(value):
        from ..accounting.invariants import cents_to_money

        return cents_to_money(int(value))

    @staticmethod
    def _now():
        from ..accounting.service import now

        return now()

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        from ..accounting.service import now

        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
