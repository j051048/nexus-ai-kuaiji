"""Deterministic B03 posting command and ledger read models.

Formal vouchers are append-only. Draft review, internal posting and external
delivery remain three separate state axes; this module never changes
drafts.status=exported or triggers delivery.
"""

import hashlib
import json
from datetime import date
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents, validate_period
from ..accounting.service import AccountingService, REVIEW_POLICY_VERSION
from ..accounting.review_policy import ReviewPolicyService
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
        review_receipt=None,
        review_request_id=None,
        periodic_review_id=None,
    ):
        if not review_receipt:
            raise AccountingError("正式凭证缺少版本绑定的人工复核凭据", 409)
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
            "command_id,posted_by,posted_at,review_request_id,periodic_review_id,review_mode,"
            "review_maker_id,review_reviewer_id,voucher_review_policy_version,review_completion_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,'posted',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                review_request_id,
                periodic_review_id,
                review_receipt["mode"],
                review_receipt["maker_user_id"],
                review_receipt["reviewer_user_id"],
                review_receipt["policy_version"],
                voucher_id,
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
            receipt = ReviewPolicyService.approved_receipt(conn, draft)
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
                review_receipt=receipt,
            )

    def post_manual(self, actor, request: ManualVoucher):
        with self.db.transaction(write=True, actor=actor) as conn:
            return self.post_manual_in_transaction(conn, actor, request)

    def post_manual_in_transaction(
        self, conn, actor, request: ManualVoucher, *, periodic_review_id=None
    ):
        """Post a manual voucher on an existing write transaction.

        Periodic drafts call this so voucher creation, draft status, run status
        and audit commit or roll back together.
        """
        fingerprint = _fingerprint(request.model_dump())
        book, _ = self._book(conn, actor, request.book_id, write=True)
        replay = self._command_replay(conn, actor, book["id"], request.command_key, fingerprint)
        if replay:
            return replay
        if periodic_review_id:
            receipt = self._periodic_receipt(conn, request, periodic_review_id)
        else:
            from .manual_review import ManualReviewService

            receipt = ManualReviewService(self.db).receipt_in_transaction(conn, actor, request)
        if (
            request.review_request_id
            and conn.execute(
                "SELECT 1 FROM vouchers WHERE review_request_id=?", (request.review_request_id,)
            ).fetchone()
        ):
            raise AccountingError("该手工复核请求已经正式记账，请读取原命令回执", 409)
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
            source_kind=request.relation_kind or "manual",
            source_id=request.original_voucher_id if request.relation_kind == "reversal" else None,
            reversal_of=request.original_voucher_id,
            relation_kind=request.relation_kind,
            command_key=request.command_key,
            request_fingerprint=fingerprint,
            review_receipt=receipt,
            review_request_id=request.review_request_id,
            periodic_review_id=periodic_review_id,
        )
        if original:
            conn.execute(
                "INSERT INTO voucher_relations(id,original_voucher_id,related_voucher_id,"
                "kind,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    original["id"],
                    view["voucher"]["id"],
                    request.relation_kind,
                    request.reason,
                    actor.user_id,
                    self._now(),
                ),
            )
            return self._voucher_view(conn, view["voucher"]["id"])
        return view

    def _periodic_receipt(self, conn, request, identifier):
        draft = conn.execute(
            "SELECT * FROM periodic_drafts WHERE id=? AND book_id=?", (identifier, request.book_id)
        ).fetchone()
        if (
            not draft
            or draft["status"] != "approved"
            or not draft["approved_by"]
            or draft["approved_by"] == draft["created_by"]
        ):
            raise AccountingError("期末凭证缺少独立复核凭据", 409)
        if not conn.execute(
            "SELECT 1 FROM review_authority_snapshots WHERE subject_kind='periodic' AND subject_id=? AND reviewed_version=? AND book_id=? AND proof_kind='review_time'",
            (identifier, draft["version"], request.book_id),
        ).fetchone():
            raise AccountingError("历史期末凭证缺少事务所复核依据，请重新准备与复核", 409)
        rows = conn.execute(
            "SELECT * FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no", (identifier,)
        ).fetchall()
        expected = [
            (
                r["account_id"],
                r["account_version"],
                r["debit_cents"],
                r["credit_cents"],
                json.loads(r["aux_json"] or "{}"),
                r["source_ref"],
            )
            for r in rows
        ]
        actual = [
            (
                r.account_id,
                r.account_version,
                money_to_cents(r.debit),
                money_to_cents(r.credit),
                r.auxiliary_values,
                r.source_ref,
            )
            for r in request.lines
        ]
        if (
            expected != actual
            or request.description != draft["description"]
            or request.voucher_type != "转"
            or request.relation_kind
        ):
            raise AccountingError("期末复核内容与记账请求不一致", 409)
        return {
            "mode": "independent",
            "maker_user_id": draft["created_by"],
            "reviewer_user_id": draft["approved_by"],
            "policy_version": 0,
        }

    def reverse_voucher(self, actor, voucher_id, request: ReverseVoucher):
        if not request.review_request_id:
            raise AccountingError("冲销凭证须先提交手工待审版本并完成复核", 409)
        from .manual_review import ManualReviewService

        with self.db.transaction(actor=actor) as conn:
            row = ManualReviewService(self.db)._request(
                conn, actor, request.review_request_id, "review"
            )
            payload = ManualVoucher(**json.loads(row["content_json"]), review_request_id=row["id"])
            if (
                payload.original_voucher_id != voucher_id
                or payload.relation_kind != "reversal"
                or payload.voucher_date != request.voucher_date
                or payload.command_key != request.command_key
                or payload.reason != request.reason
            ):
                raise AccountingError("冲销请求与已复核内容不一致", 409)
        return self.post_manual(actor, payload)

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
            return self.trial_balance_in_transaction(conn, actor, book_id, as_of)

    def trial_balance_in_transaction(self, conn, actor, book_id, as_of=None):
        """Read all balance inputs under the caller's transaction and locks."""
        book, _ = self._book(conn, actor, book_id)
        self._check_read_date(book, as_of)
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
        return {
            **self._trial_balance_rows(conn, binding, opening, voucher_rows, book["period"]),
            "scope": self._read_scope(book),
        }

    @staticmethod
    def _read_scope(book):
        return {
            "book_id": book["id"],
            "firm_id": book["firm_id"],
            "company_id": book["company_id"],
            "period": book["period"],
        }

    @staticmethod
    def _check_read_date(book, as_of):
        if as_of is None:
            return
        try:
            valid = date.fromisoformat(as_of).isoformat() == as_of and as_of[:7] == book["period"]
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise AccountingError("截止日期必须是当前会计月份内的有效日期")

    def _accounts(self, conn, ledger_id, period=None):
        """Account identity as of one period, or the current versions.

        A month keeps the version whose effective window covers it, so a later
        rename, re-parenting or leaf split cannot rewrite a posted history. The
        highest covering version wins when a month has several candidates.
        """

        rows = conn.execute(
            "SELECT a.id,a.current_version,v.version,v.code,v.name,v.parent_account_id,"
            "v.level,v.is_leaf,v.category,v.normal_balance,v.effective_from,v.effective_to "
            "FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id "
            "WHERE a.ledger_id=?",
            (ledger_id,),
        ).fetchall()
        picked = {}
        for row in rows:
            item = dict(row)
            if period is None:
                if int(item["version"]) != int(item["current_version"]):
                    continue
            else:
                start = item["effective_from"]
                end = item["effective_to"]
                if (start and start > period) or (end and end < period):
                    continue
            chosen = picked.get(item["id"])
            if chosen is None or int(item["version"]) > int(chosen["version"]):
                picked[item["id"]] = item
        for item in picked.values():
            item.pop("current_version", None)
        return picked

    def _accounts_for_periods(self, conn, ledger_id, periods):
        """Union of the per-period identities, newest period keeping its view."""

        merged = {}
        for period in periods:
            for account_id, meta in self._accounts(conn, ledger_id, period).items():
                merged[account_id] = meta
        return merged

    def _trial_balance_rows(self, conn, binding, opening, voucher_rows, period=None):
        accounts = self._accounts(conn, binding["ledger_id"], period)
        own = {account_id: {"opening": 0, "debit": 0, "credit": 0} for account_id in accounts}
        for row in opening:
            if row["account_id"] in own:
                own[row["account_id"]]["opening"] += int(row["opening_cents"])
        for row in voucher_rows:
            if row["account_id"] in own:
                own[row["account_id"]]["debit"] += int(row["debit_cents"])
                own[row["account_id"]]["credit"] += int(row["credit_cents"])
        return self._balance_rows(accounts, own)

    @staticmethod
    def _balance_rows(accounts, own):
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
        # The ledger's own movement never depends on how the tree is shaped:
        # summing the leaf rows loses a parent's own posting the moment a child
        # is added to it, while the parent row stays visible.
        totals_out = {
            "opening_cents": sum(values["opening"] for values in own.values()),
            "debit_cents": sum(values["debit"] for values in own.values()),
            "credit_cents": sum(values["credit"] for values in own.values()),
        }
        totals_out["closing_cents"] = (
            totals_out["opening_cents"] + totals_out["debit_cents"] - totals_out["credit_cents"]
        )
        return {"rows": rows, "totals": totals_out}

    # ---------- continuous period ranges ----------

    BLOCKER_TEXT = {
        "missing_period": "该月份尚未建立账套，无法形成连续账簿",
        "unauthorized": "当前账号无权查看该月份账套",
        "opening_missing": "该月份尚未绑定冻结期初",
        "period_open": "该月份尚未关账，结果不能当作已结账账簿使用",
        "opening_mismatch": "该月份期初与上一期间期末不一致",
        "continuity_unverified": "上期存在银行或子账证据，但本期缺少已完成的期间桥接，结果不能当作完整账簿",
    }

    @staticmethod
    def _aux_key(value):
        """Canonical grouping key for one auxiliary dimension payload."""

        if not value:
            return ""
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value
        if not isinstance(value, dict):
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        return json.dumps(
            {str(key): str(item) for key, item in sorted(value.items())},
            sort_keys=True,
            ensure_ascii=False,
        )

    @staticmethod
    def _bridge_required(conn, book_id):
        """A period only needs a bridge when the period before it had evidence."""

        for sql in (
            "SELECT 1 FROM bank_accounts WHERE book_id=? LIMIT 1",
            "SELECT 1 FROM subledger_items WHERE book_id=? LIMIT 1",
        ):
            if conn.execute(sql, (book_id,)).fetchone():
                return True
        return False

    @staticmethod
    def _bridge_completed(conn, book_id, source_book_id):
        return bool(
            conn.execute(
                "SELECT 1 FROM subledger_period_bridges p"
                " JOIN subledger_bridge_completions c ON c.bridge_id=p.id"
                " WHERE p.book_id=? AND p.source_book_id=? LIMIT 1",
                (book_id, source_book_id),
            ).fetchone()
        )

    def _opening_map(self, conn, package_version_id):
        """Opening balances keyed by account *and* auxiliary object."""

        return {
            (row["account_id"], self._aux_key(row["aux_json"])): int(row["total"])
            for row in conn.execute(
                "SELECT account_id,aux_json,COALESCE(SUM(opening_cents),0) AS total "
                "FROM opening_lines WHERE package_version_id=? "
                "GROUP BY account_id,aux_json",
                (package_version_id,),
            )
        }

    def _movement_map(self, conn, book_id):
        return {
            (row["account_id"], self._aux_key(row["aux_json"])): (
                int(row["debit"]),
                int(row["credit"]),
            )
            for row in conn.execute(
                "SELECT l.account_id,l.aux_json,COALESCE(SUM(l.debit_cents),0) AS debit,"
                "COALESCE(SUM(l.credit_cents),0) AS credit FROM voucher_lines l "
                "JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? "
                "GROUP BY l.account_id,l.aux_json",
                (book_id,),
            )
        }

    @staticmethod
    def _closing_map(opening, movement):
        closed = {
            key: value + movement.get(key, (0, 0))[0] - movement.get(key, (0, 0))[1]
            for key, value in opening.items()
        }
        for key, (debit, credit) in movement.items():
            closed.setdefault(key, debit - credit)
        return closed

    def _boundary_blockers(
        self, conn, source_book_id, opening, movement, carried, target_book_id, hint=""
    ):
        """Compare one month boundary; the first month of a range needs it too.

        A window that starts mid-year must not look complete only because the
        broken boundary sits just outside it.
        """

        expected = {
            key: value for key, value in self._closing_map(opening, movement).items() if value
        }
        against = {key: value for key, value in carried.items() if value}
        codes = []
        if expected != against:
            codes.append(("opening_mismatch", self.BLOCKER_TEXT["opening_mismatch"] + hint))
        if self._bridge_required(conn, source_book_id) and not (
            self._bridge_completed(conn, target_book_id, source_book_id)
        ):
            codes.append(
                ("continuity_unverified", self.BLOCKER_TEXT["continuity_unverified"] + hint)
            )
        return codes

    def _prior_boundary(self, conn, actor, ledger_id, start, first, capability):
        """Check the boundary the window hides, when the month before it exists."""

        row = conn.execute(
            "SELECT period,book_id FROM ledger_periods WHERE ledger_id=? AND period<? "
            "ORDER BY period DESC LIMIT 1",
            (ledger_id, start),
        ).fetchone()
        if row is None:
            return []
        try:
            self.accounting.access.book(conn, actor, row["book_id"], capability)
            binding = self._binding(conn, row["book_id"])
        except AccountingError:
            # An invisible or unbound prior month cannot be verified here; the
            # range says nothing about it rather than guessing.
            return []
        return [
            {"code": code, "period": first["period"], "message": message}
            for code, message in self._boundary_blockers(
                conn,
                row["book_id"],
                self._opening_map(conn, binding["package_version_id"]),
                self._movement_map(conn, row["book_id"]),
                first["_opening"],
                first["book_id"],
                f"（上一期间 {row['period']}）",
            )
        ]

    @staticmethod
    def _months(start, end):
        try:
            validate_period(start)
            validate_period(end)
        except ValueError as error:
            raise AccountingError(str(error)) from error
        if start > end:
            raise AccountingError("开始期间不能晚于结束期间")
        year, month = int(start[:4]), int(start[5:])
        last_year, last_month = int(end[:4]), int(end[5:])
        months = []
        while (year, month) <= (last_year, last_month):
            months.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            if len(months) > 240:
                raise AccountingError("连续账簿最多查询 240 个月")
        return months

    def _range_coverage(self, conn, actor, ledger_id, start, end, capability="read"):
        """Enumerate every month in the range; never silently drop a gap.

        Each month is classified as ok, missing, unauthorized, unbound or open.
        Blockers are returned to the caller instead of being folded into a
        total that would look complete.
        """

        months = self._months(start, end)
        if not conn.execute("SELECT 1 FROM ledgers WHERE id=?", (ledger_id,)).fetchone():
            raise AccountingError("长期账套不存在或无权访问", 404)
        linked = {
            row["period"]: row["book_id"]
            for row in conn.execute(
                "SELECT period,book_id FROM ledger_periods WHERE ledger_id=? "
                "AND period BETWEEN ? AND ? ORDER BY period",
                (ledger_id, start, end),
            )
        }
        items, blockers, visible = [], [], 0
        for period in months:
            book_id = linked.get(period)
            item = {
                "period": period,
                "book_id": book_id,
                "status": "missing",
                "opening_cents": 0,
                "debit_cents": 0,
                "credit_cents": 0,
                "closing_cents": 0,
            }
            if book_id is None:
                blockers.append(
                    {
                        "code": "missing_period",
                        "period": period,
                        "message": self.BLOCKER_TEXT["missing_period"],
                    }
                )
            else:
                try:
                    book = self.accounting.access.book(conn, actor, book_id, capability)
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
                    item["status"] = "unauthorized"
                    blockers.append(
                        {
                            "code": "unauthorized",
                            "period": period,
                            "message": self.BLOCKER_TEXT["unauthorized"],
                        }
                    )
                else:
                    item["status"] = "ok"
                    item["book"] = book
                    visible += 1
            items.append(item)
        if not visible:
            raise AccountingError("长期账套不存在或无权访问", 404)
        for item in items:
            if item["status"] != "ok":
                continue
            try:
                binding = self._binding(conn, item["book_id"])
            except AccountingError:
                item["status"] = "unbound"
                blockers.append(
                    {
                        "code": "opening_missing",
                        "period": item["period"],
                        "message": self.BLOCKER_TEXT["opening_missing"],
                    }
                )
                continue
            item["binding"] = binding
            # Continuity is keyed by account *and* auxiliary object: a balanced
            # package can move value between two counterparts of one account
            # and an account-level total would hide exactly that shift.
            item["_opening"] = self._opening_map(conn, binding["package_version_id"])
            item["_movement"] = self._movement_map(conn, item["book_id"])
            item["opening_cents"] = sum(item["_opening"].values())
            item["debit_cents"] = sum(debit for debit, _ in item["_movement"].values())
            item["credit_cents"] = sum(credit for _, credit in item["_movement"].values())
            item["closing_cents"] = (
                item["opening_cents"] + item["debit_cents"] - item["credit_cents"]
            )
            if item["book"]["period_status"] != "closed":
                blockers.append(
                    {
                        "code": "period_open",
                        "period": item["period"],
                        "message": self.BLOCKER_TEXT["period_open"],
                    }
                )
        first = next((item for item in items if item["status"] == "ok"), None)
        if first is not None:
            blockers.extend(self._prior_boundary(conn, actor, ledger_id, start, first, capability))
        previous = None
        for item in items:
            if item["status"] != "ok":
                continue
            if previous is not None:
                for code, message in self._boundary_blockers(
                    conn,
                    previous["book_id"],
                    previous["_opening"],
                    previous["_movement"],
                    item["_opening"],
                    item["book_id"],
                ):
                    blockers.append({"code": code, "period": item["period"], "message": message})
            previous = item
        blockers.sort(key=lambda blocker: (blocker["period"], blocker["code"]))
        return {
            "months": months,
            "items": items,
            "blockers": blockers,
            "complete": not blockers,
        }

    @staticmethod
    def _coverage_view(coverage):
        return [
            {key: value for key, value in item.items() if key != "book" and not key.startswith("_")}
            for item in coverage["items"]
        ]

    @staticmethod
    def _range_shape(coverage, rows):
        return {
            "coverage": [
                {
                    key: item[key]
                    for key in (
                        "period",
                        "status",
                        "opening_cents",
                        "debit_cents",
                        "credit_cents",
                        "closing_cents",
                    )
                }
                for item in coverage["items"]
            ],
            "blockers": coverage["blockers"],
            "rows": [
                {
                    key: row[key]
                    for key in (
                        "account_id",
                        "code",
                        "opening_cents",
                        "debit_cents",
                        "credit_cents",
                        "closing_cents",
                    )
                }
                for row in rows
            ],
        }

    @staticmethod
    def _range_scope(coverage, ledger_id):
        """Scope of the first readable month; an incomplete range still has one."""

        for item in coverage["items"]:
            book = item.get("book")
            if book:
                return {
                    "firm_id": book["firm_id"],
                    "company_id": book["company_id"],
                    "ledger_id": ledger_id,
                    "book_id": item["book_id"],
                    "start": coverage["months"][0],
                    "end": coverage["months"][-1],
                    "complete": coverage["complete"],
                }
        raise AccountingError("长期账套不存在或无权访问", 404)

    def _snapshot_names(self, conn, book_ids):
        names = {}
        if not book_ids:
            return names
        marks = ",".join("?" for _ in book_ids)
        for row in conn.execute(
            "SELECT DISTINCT l.account_id,l.name_snapshot FROM voucher_lines l "
            f"JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id IN ({marks})",
            tuple(book_ids),
        ):
            names.setdefault(row["account_id"], set()).add(row["name_snapshot"])
        return names

    def range_trial_balance(self, actor, ledger_id, start, end):
        with self.db.transaction(actor=actor) as conn:
            return self.range_trial_balance_in_transaction(conn, actor, ledger_id, start, end)

    def range_trial_balance_in_transaction(
        self, conn, actor, ledger_id, start, end, capability="read"
    ):
        """Balance for [start, end] aggregated from the first period's opening.

        The opening package of a later month repeats the closing balance of the
        month before it; adding those packages again would double count, so only
        the first visible period contributes opening amounts.
        """

        coverage = self._range_coverage(conn, actor, ledger_id, start, end, capability)
        ok_items = [item for item in coverage["items"] if item["status"] == "ok"]
        periods = [item["period"] for item in ok_items] or [coverage["months"][-1]]
        accounts = self._accounts_for_periods(conn, ledger_id, periods)
        own = {account_id: {"opening": 0, "debit": 0, "credit": 0} for account_id in accounts}
        for item in ok_items[:1]:
            for row in conn.execute(
                "SELECT account_id,opening_cents FROM opening_lines WHERE package_version_id=?",
                (item["binding"]["package_version_id"],),
            ):
                if row["account_id"] in own:
                    own[row["account_id"]]["opening"] += int(row["opening_cents"])
        book_ids = [item["book_id"] for item in ok_items]
        names = self._snapshot_names(conn, book_ids)
        if book_ids:
            marks = ",".join("?" for _ in book_ids)
            for row in conn.execute(
                "SELECT account_id,debit_cents,credit_cents FROM voucher_lines l "
                f"JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id IN ({marks})",
                tuple(book_ids),
            ):
                if row["account_id"] in own:
                    own[row["account_id"]]["debit"] += int(row["debit_cents"])
                    own[row["account_id"]]["credit"] += int(row["credit_cents"])
        data = self._balance_rows(accounts, own)
        for row in data["rows"]:
            seen = names.get(row["account_id"], set())
            row["names"] = sorted(seen | {row["name"]})
            row["renamed"] = any(name != row["name"] for name in seen)
        return {
            **data,
            "scope": self._range_scope(coverage, ledger_id),
            "start": coverage["months"][0],
            "end": coverage["months"][-1],
            "complete": coverage["complete"],
            "coverage": self._coverage_view(coverage),
            "blockers": coverage["blockers"],
            "fingerprint": _fingerprint(self._range_shape(coverage, data["rows"])),
        }

    def range_account_ledger(self, actor, ledger_id, account_id, start, end):
        with self.db.transaction(actor=actor) as conn:
            return self.range_account_ledger_in_transaction(
                conn, actor, ledger_id, account_id, start, end
            )

    def range_account_ledger_in_transaction(
        self, conn, actor, ledger_id, account_id, start, end, capability="read"
    ):
        coverage = self._range_coverage(conn, actor, ledger_id, start, end, capability)
        ok_items = [item for item in coverage["items"] if item["status"] == "ok"]
        periods = [item["period"] for item in ok_items] or [coverage["months"][-1]]
        accounts = self._accounts_for_periods(conn, ledger_id, periods)
        if account_id not in accounts:
            raise AccountingError("科目不存在或不属于当前账套", 404)
        opening = 0
        for item in ok_items[:1]:
            opening = int(
                conn.execute(
                    "SELECT COALESCE(SUM(opening_cents),0) AS total FROM opening_lines "
                    "WHERE package_version_id=? AND account_id=?",
                    (item["binding"]["package_version_id"], account_id),
                ).fetchone()["total"]
            )
        running = opening
        entries = []
        for item in coverage["items"]:
            if item["status"] != "ok":
                continue
            for row in conn.execute(
                "SELECT v.id AS voucher_id,v.voucher_date,v.voucher_no,v.voucher_type,"
                "v.description,l.debit_cents,l.credit_cents,l.aux_json,l.line_no "
                "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
                "WHERE v.book_id=? AND l.account_id=? "
                "ORDER BY v.voucher_date,v.voucher_type,v.voucher_no,l.line_no",
                (item["book_id"], account_id),
            ):
                running += int(row["debit_cents"]) - int(row["credit_cents"])
                entries.append({**dict(row), "period": item["period"], "balance_cents": running})
        shape = {
            **self._range_shape(coverage, []),
            "account_id": account_id,
            "opening_cents": opening,
            "entries": [
                {
                    key: entry[key]
                    for key in (
                        "voucher_id",
                        "debit_cents",
                        "credit_cents",
                        "balance_cents",
                    )
                }
                for entry in entries
            ],
        }
        return {
            "account": {
                "id": account_id,
                "code": accounts[account_id]["code"],
                "name": accounts[account_id]["name"],
            },
            "opening_cents": opening,
            "entries": entries,
            "closing_cents": running,
            "scope": self._range_scope(coverage, ledger_id),
            "start": coverage["months"][0],
            "end": coverage["months"][-1],
            "complete": coverage["complete"],
            "coverage": self._coverage_view(coverage),
            "blockers": coverage["blockers"],
            "fingerprint": _fingerprint(shape),
        }

    def account_ledger(self, actor, book_id, account_id, as_of=None):
        with self.db.transaction(actor=actor) as conn:
            return self.account_ledger_in_transaction(conn, actor, book_id, account_id, as_of)

    def account_ledger_in_transaction(self, conn, actor, book_id, account_id, as_of=None):
        book, _ = self._book(conn, actor, book_id)
        self._check_read_date(book, as_of)
        binding = self._binding(conn, book["id"])
        if not conn.execute(
            "SELECT 1 FROM ledger_accounts WHERE id=? AND ledger_id=?",
            (account_id, binding["ledger_id"]),
        ).fetchone():
            raise AccountingError("科目不存在或不属于当前账套", 404)
        opening = conn.execute(
            "SELECT COALESCE(SUM(opening_cents),0) AS total FROM opening_lines "
            "WHERE package_version_id=? AND account_id=?",
            (binding["package_version_id"], account_id),
        ).fetchone()["total"]
        rows = conn.execute(
            "SELECT v.id AS voucher_id,v.voucher_date,v.voucher_no,v.voucher_type,v.description,"
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
        return {
            "opening_cents": int(opening),
            "entries": entries,
            "closing_cents": running,
            "scope": self._read_scope(book),
        }

    def daily_journal(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self.daily_journal_in_transaction(conn, actor, book_id)

    def daily_journal_in_transaction(self, conn, actor, book_id):
        book, _ = self._book(conn, actor, book_id)
        binding = self._binding(conn, book["id"])
        rows = conn.execute(
            "SELECT v.id AS voucher_id,v.voucher_date,v.voucher_no,v.voucher_type,v.description,"
            "l.account_id,l.code_snapshot,l.name_snapshot,l.debit_cents,l.credit_cents,"
            "l.aux_json,l.line_no FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND (l.code_snapshot LIKE ? OR l.code_snapshot LIKE ?) "
            "ORDER BY v.voucher_date,v.voucher_type,v.voucher_no,l.line_no",
            (book["id"], "1001%", "1002%"),
        ).fetchall()
        opening = {
            row["account_id"]: int(row["total"])
            for row in conn.execute(
                "SELECT ol.account_id,COALESCE(SUM(ol.opening_cents),0) AS total "
                "FROM opening_lines ol JOIN ledger_account_versions av "
                "ON av.account_id=ol.account_id AND av.version=ol.account_version "
                "WHERE ol.package_version_id=? AND (av.code LIKE ? OR av.code LIKE ?) "
                "GROUP BY ol.account_id",
                (binding["package_version_id"], "1001%", "1002%"),
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
            return self.aux_ledger_in_transaction(conn, actor, book_id, auxiliary_values)

    def aux_ledger_in_transaction(self, conn, actor, book_id, auxiliary_values):
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
        return {"opening": opening, "vouchers": vouchers, "scope": self._read_scope(book)}

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
