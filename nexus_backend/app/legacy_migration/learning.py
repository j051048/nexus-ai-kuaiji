"""Independently reviewed historical examples produce bounded bank rules only.

The original vendor's ``posted`` marker is provenance, never our approval.
No historical example is inserted into ordinary learning_samples or sources.
"""

import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator, model_validator

from app.accounting.access import AccessControl
from app.accounting.catalog import CatalogService
from app.accounting.domain import AccountingError
from app.accounting.models import InputModel, cents_to_money, money_to_cents
from app.accounting.queries import page_bounds
from app.accounting.service import fingerprint, now
from app.learning.models import RuleDefinition
from app.learning.service import LearningService, canonical_lines
from .normalization import signed_cents


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class LegacyLearningCommand(InputModel):
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("须说明独立会计判断及资料依据")
        return value.strip()


class LegacyCandidateCreate(LegacyLearningCommand):
    definition: RuleDefinition
    positive_record_ids: list[StrictStr] = Field(min_length=1, max_length=100)

    @field_validator("positive_record_ids")
    @classmethod
    def identifiers(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 100 for item in value):
            raise ValueError("历史记录 ID 不可重复、为空或超过 100 字")
        return sorted(value)

    @model_validator(mode="after")
    def bounded_bank(self):
        d = self.definition
        if d.source_kind != "bank" or len(d.lines) != 2 or not d.conditions.counterparty_id:
            raise ValueError("历史规则仅支持明确银行收付、稳定往来方和两行总额分录")
        return self


class LegacyLearningVersion(LegacyLearningCommand):
    expected_version: int = Field(strict=True, ge=1)


class LegacyLearningReview(LegacyLearningVersion):
    decision: StrictStr = Field(pattern="^(approve|reject)$")
    evidence_confirmed: bool = Field(strict=True)

    @model_validator(mode="after")
    def evidence(self):
        if not self.evidence_confirmed:
            raise ValueError("须独立核对历史原件，不能仅依据旧系统已记账标志")
        return self


class LegacyLearningShadow(LegacyLearningVersion):
    negative_record_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    evidence_confirmed: bool = Field(strict=True)
    _ids = field_validator("negative_record_ids")(LegacyCandidateCreate.identifiers.__func__)

    @model_validator(mode="after")
    def evidence(self):
        if not self.evidence_confirmed:
            raise ValueError("须独立核对负例及原件后回放")
        return self


class LegacyLearningPublish(LegacyLearningVersion):
    shadow_id: StrictStr = Field(min_length=1, max_length=100)


class LegacyLearningRetire(LegacyLearningVersion):
    pass


class LegacyLearningService:
    def __init__(self, db, migration_service):
        self.db, self.migration = db, migration_service
        self.access, self.catalog, self.learning = (
            AccessControl(),
            CatalogService(db),
            LearningService(db),
        )

    def _project(self, conn, actor, book_id, project_id, write=False):
        project, target, permissions = self.migration._project(
            conn, actor, book_id, project_id, "read"
        )
        book = self.access.book(conn, actor, book_id, "review" if write else "read")
        grant = conn.execute(
            "SELECT role FROM migration_project_access WHERE project_id=? AND user_id=?",
            (project_id, actor.user_id),
        ).fetchone()
        learning_review = bool(
            permissions["manage"] or grant and grant["role"] in {"manager", "reviewer"}
        )
        if write and (not learning_review or book["period_status"] != "open"):
            raise AccountingError("历史样例操作需要本项目当前会计复核权限和开放期间", 403)
        cutover = conn.execute(
            "SELECT * FROM migration_cutovers WHERE project_id=? AND book_id=?",
            (project_id, book_id),
        ).fetchone()
        if project["status"] != "active" or not cutover:
            raise AccountingError("须先完成独立迁移验收及接管，再使用历史样例", 409)
        return project, book, dict(cutover), json.loads(cutover["payload_json"])

    def _candidate(self, conn, actor, book_id, project_id, candidate_id, write=False):
        context = self._project(conn, actor, book_id, project_id, write)
        row = conn.execute(
            "SELECT * FROM migration_learning_candidates WHERE id=? AND project_id=? AND book_id=? AND firm_id=?",
            (candidate_id, project_id, book_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("历史候选不存在或当前无权访问", 404)
        return dict(row), context

    @staticmethod
    def _expect(row, body):
        if row["version"] != body.expected_version:
            raise AccountingError("候选版本已改变，请刷新后处理", 409)

    def _records(self, conn, project_id, identifiers):
        base, params = self.migration._record_base(project_id)
        marks = ",".join("?" for _ in identifiers)
        rows = conn.execute(
            "SELECT r.*" + base + " AND r.id IN (" + marks + ")", [*params, *identifiers]
        ).fetchall()
        if len(rows) != len(identifiers):
            raise AccountingError("所选历史记录不存在、已替代或当前无权访问", 404)
        return {r["id"]: dict(r) for r in rows}

    def _verify_sources(self, actor, book_id, project_id, identifiers):
        """Read only selected originals outside the write transaction and lock."""
        with self.db.transaction(actor=actor) as conn:
            self._project(conn, actor, book_id, project_id)
            records = self._records(conn, project_id, identifiers)
            originals = [
                dict(self.migration._file(conn, actor, book_id, project_id, key))
                for key in sorted({r["file_id"] for r in records.values()})
            ]
        for original in originals:
            self.migration._read_blob(original)

    def _verify_candidate_sources(
        self, actor, book_id, project_id, candidate_id, negative_ids=None, shadow_id=None
    ):
        with self.db.transaction(actor=actor) as conn:
            row, _ = self._candidate(conn, actor, book_id, project_id, candidate_id)
            identifiers = json.loads(row["positive_record_ids_json"])
            if shadow_id:
                shadow = conn.execute(
                    "SELECT negative_record_ids_json FROM migration_learning_shadows WHERE id=? AND candidate_id=?",
                    (shadow_id, candidate_id),
                ).fetchone()
                if not shadow:
                    raise AccountingError("回放不存在或当前无权访问", 404)
                identifiers += json.loads(shadow[0])
            identifiers += negative_ids or []
        self._verify_sources(actor, book_id, project_id, sorted(set(identifiers)))

    def _fact(self, conn, project, book, cutover, snapshot, record):
        """A narrow projection requires bank and counterparty identities in original lines."""
        if self.db.is_postgres:
            value = conn.execute(
                "SELECT accounting.migration_learning_fact(?,?)", (project["id"], record["id"])
            ).fetchone()[0]
            return json.loads(value) if isinstance(value, str) else value
        payload = json.loads(record["payload_json"])
        lines = payload.get("lines", [])
        if (
            record["kind"] != "voucher"
            or payload.get("status") != "posted"
            or payload.get("is_reversal")
            or len(lines) != 2
        ):
            return None
        if any(
            "结转" in str(line.get("description", "")) or "损益" in str(line.get("description", ""))
            for line in lines
        ):
            return None
        bank = [
            line
            for line in lines
            if line["account_code"].startswith("1002")
            or line.get("auxiliaries", {}).get("bank_account")
        ]
        if len(bank) != 1:
            return None
        counterparties = {
            line.get("auxiliaries", {}).get("counterparty")
            for line in lines
            if line.get("auxiliaries", {}).get("counterparty")
        }
        if len(counterparties) != 1 or any(
            set(line.get("auxiliaries", {})) - {"counterparty", "bank_account"} for line in lines
        ):
            return None
        cp_source = next(iter(counterparties))
        bindings = [
            item
            for item in snapshot.get("master_bindings", [])
            if item["kind"] == "counterparty" and item["source_code"] == cp_source
        ]
        if len(bindings) != 1:
            return None
        cp = conn.execute(
            "SELECT id,name FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind='counterparty' AND active=1",
            (bindings[0]["target_id"], project["firm_id"], project["company_id"]),
        ).fetchone()
        if not cp:
            return None
        mapping = {
            item["source_code"]: item["target_code"]
            for item in snapshot.get("account_bindings", [])
        }
        final = []
        debit = credit = 0
        for line in lines:
            try:
                d, c = signed_cents(line["debit"]), signed_cents(line["credit"])
            except (ValueError, TypeError):
                return None
            if d < 0 or c < 0 or (d == 0) == (c == 0):
                return None
            debit += d
            credit += c
            target = mapping.get(line["account_code"])
            account = conn.execute(
                "SELECT auxiliary_requirements FROM accounts WHERE book_id=? AND code=? AND active=1",
                (book["id"], target),
            ).fetchone()
            if not account or set(json.loads(account["auxiliary_requirements"])) - {"counterparty"}:
                return None
            aux = (
                {"counterparty": cp["name"]}
                if "counterparty" in json.loads(account["auxiliary_requirements"])
                else {}
            )
            final.append(
                {
                    "account_code": target,
                    "debit": cents_to_money(d),
                    "credit": cents_to_money(c),
                    "auxiliary_values": aux,
                }
            )
        if debit <= 0 or debit != credit:
            return None
        bank_target = mapping.get(bank[0]["account_code"])
        bank_account = conn.execute(
            "SELECT category FROM accounts WHERE book_id=? AND code=? AND active=1",
            (book["id"], bank_target),
        ).fetchone()
        if not bank_account or bank_account["category"] != "asset":
            return None
        direction = "expense" if signed_cents(bank[0]["credit"]) > 0 else "income"
        return {
            "snapshot": {
                "source_type": "historical",
                "source_kind": "bank",
                "business_type": "daily_cashflow",
                "counterparty_id": cp["id"],
                "payload": {
                    "occurred_on": payload["date"],
                    "amount": cents_to_money(debit),
                    "direction": direction,
                    "payment_status": "paid",
                    "counterparty": cp["name"],
                },
                "context": {},
                "recognized": {},
            },
            "final": {"lines": final},
        }

    def _save_sample(self, conn, actor, row, context, record, polarity):
        project, book, cutover, snapshot = context
        fact = self._fact(conn, project, book, cutover, snapshot, record)
        quality = {
            "source_type": "historical",
            "eligible": fact is not None,
            "independent_review_required": True,
            "vendor_posted_is_not_approval": True,
        }
        fp = fingerprint(
            {
                "record_id": record["id"],
                "record_fingerprint": record["fingerprint"],
                "cutover_id": cutover["id"],
                "fact": fact,
            }
        )
        existing = conn.execute(
            "SELECT id,fingerprint,polarity FROM migration_learning_samples WHERE candidate_id=? AND record_id=?",
            (row["id"], record["id"]),
        ).fetchone()
        if existing:
            if existing["fingerprint"] != fp or existing["polarity"] != polarity:
                raise AccountingError("样例身份或当前资料已改变，请建立新候选", 409)
            return fact
        conn.execute(
            "INSERT INTO migration_learning_samples(id,candidate_id,record_id,project_id,book_id,firm_id,company_id,polarity,snapshot_json,final_json,quality_json,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                row["id"],
                record["id"],
                project["id"],
                book["id"],
                project["firm_id"],
                project["company_id"],
                polarity,
                encode(fact["snapshot"] if fact else {}),
                encode(fact["final"] if fact else {}),
                encode(quality),
                fp,
                actor.user_id,
                now(),
            ),
        )
        return fact

    def _context(self, conn, actor, row):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.migration_learning_context(?)", (row["id"],)
            ).fetchone()[0]
        book = self.access.book(conn, actor, row["book_id"])
        samples = [
            dict(r)
            for r in conn.execute(
                "SELECT record_id,fingerprint,polarity FROM migration_learning_samples WHERE candidate_id=? ORDER BY record_id",
                (row["id"],),
            )
        ]
        accounts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM accounts WHERE book_id=? ORDER BY code", (row["book_id"],)
            )
        ]
        masters = [
            dict(r)
            for r in conn.execute(
                "SELECT id,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? ORDER BY id",
                (actor.firm_id, book["company_id"]),
            )
        ]
        aliases = [
            dict(r)
            for r in conn.execute(
                "SELECT id,entity_id,status,version,original FROM company_master_aliases WHERE firm_id=? AND company_id=? ORDER BY id",
                (actor.firm_id, book["company_id"]),
            )
        ]
        review = conn.execute(
            "SELECT id,reviewed_by,decision FROM migration_learning_reviews WHERE candidate_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        return fingerprint(
            {
                "candidate": row["definition_json"],
                "samples": samples,
                "accounts": accounts,
                "masters": masters,
                "aliases": aliases,
                "period_status": book["period_status"],
                "review": dict(review) if review else None,
            }
        )

    def _json(self, conn, actor, row, book):
        review = conn.execute(
            "SELECT id,decision,reviewed_by,reason,created_at FROM migration_learning_reviews WHERE candidate_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        shadow = conn.execute(
            "SELECT id,passed,context_fingerprint,report_json,created_at FROM migration_learning_shadows WHERE candidate_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        current = bool(shadow and shadow["context_fingerprint"] == self._context(conn, actor, row))
        grant = conn.execute(
            "SELECT role FROM migration_project_access WHERE project_id=? AND user_id=?",
            (row["project_id"], actor.user_id),
        ).fetchone()
        project_permissions = self.migration._project(
            conn, actor, row["book_id"], row["project_id"], "read"
        )[2]
        can = bool(
            book["permissions"]["review"]
            and book["period_status"] == "open"
            and (
                project_permissions["manage"] or grant and grant["role"] in {"reviewer", "manager"}
            )
        )
        return {
            "id": row["id"],
            "version": row["version"],
            "status": row["status"],
            "definition": json.loads(row["definition_json"]),
            "positive_record_ids": json.loads(row["positive_record_ids_json"]),
            "eligibility": json.loads(row["eligibility_json"]),
            "reason": row["reason"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "review": dict(review) if review else None,
            "latest_shadow": {
                "id": shadow["id"],
                "passed": bool(shadow["passed"]),
                "current": current,
                "report": json.loads(shadow["report_json"]),
                "created_at": shadow["created_at"],
            }
            if shadow
            else None,
            "permissions": {
                "review": can
                and actor.user_id != row["created_by"]
                and row["status"] == "candidate",
                "shadow": can
                and actor.user_id != row["created_by"]
                and row["status"] == "reviewed",
                "publish": can
                and actor.user_id != row["created_by"]
                and row["status"] == "reviewed"
                and current
                and bool(shadow["passed"]),
                "retire": can and row["status"] == "published",
            },
        }

    def list(self, actor, book_id, project_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            _, book, _, snapshot = self._project(conn, actor, book_id, project_id)
            total = conn.execute(
                "SELECT count(*) FROM migration_learning_candidates WHERE project_id=? AND book_id=?",
                (project_id, book_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM migration_learning_candidates WHERE project_id=? AND book_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (project_id, book_id, limit, offset),
            ).fetchall()
            bound_ids = {
                item["target_id"]
                for item in snapshot.get("master_bindings", [])
                if item["kind"] == "counterparty"
            }
            counterparties = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,code,name FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind='counterparty' AND active=1 ORDER BY code,id LIMIT 500",
                    (actor.firm_id, book["company_id"]),
                )
                if r["id"] in bound_ids
            ]
            grant = conn.execute(
                "SELECT role FROM migration_project_access WHERE project_id=? AND user_id=?",
                (project_id, actor.user_id),
            ).fetchone()
            project_permissions = self.migration._project(conn, actor, book_id, project_id, "read")[
                2
            ]
            can_create = bool(
                book["permissions"]["review"]
                and book["period_status"] == "open"
                and (
                    project_permissions["manage"]
                    or grant
                    and grant["role"] in {"reviewer", "manager"}
                )
            )
            return {
                "candidates": [self._json(conn, actor, dict(r), book) for r in rows],
                "counterparties": counterparties,
                "can_create": can_create,
                "pagination": {
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "has_more": offset + len(rows) < total,
                },
            }

    def _prior(self, conn, table, scope, key, digest):
        prior = conn.execute(
            "SELECT * FROM " + table + " WHERE " + scope[0] + "=? AND request_key=?",
            (scope[1], key),
        ).fetchone()
        if prior and prior["request_fingerprint"] != digest:
            raise AccountingError("幂等键已用于不同历史学习请求", 409)
        return prior

    def create(self, actor, book_id, project_id, body):
        body = (
            body
            if isinstance(body, LegacyCandidateCreate)
            else LegacyCandidateCreate.model_validate(body)
        )
        digest = fingerprint(body.model_dump())
        self._verify_sources(actor, book_id, project_id, body.positive_record_ids)
        with self.db.transaction(write=True, actor=actor) as conn:
            context = self._project(conn, actor, book_id, project_id, True)
            project, book, _, _ = context
            prior = self._prior(
                conn,
                "migration_learning_candidates",
                ("project_id", project_id),
                body.idempotency_key,
                digest,
            )
            if prior:
                return self._json(conn, actor, dict(prior), book)
            cp_id = body.definition.conditions.counterparty_id
            if (
                not any(
                    item["kind"] == "counterparty" and item["target_id"] == cp_id
                    for item in context[3].get("master_bindings", [])
                )
                or not conn.execute(
                    "SELECT 1 FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=? AND kind='counterparty' AND active=1",
                    (cp_id, actor.firm_id, project["company_id"]),
                ).fetchone()
            ):
                raise AccountingError("规则须使用接管快照中明确映射的本企业稳定往来方", 409)
            records = self._records(conn, project_id, body.positive_record_ids)
            identities = {(r["kind"], r["period"], r["external_id"]) for r in records.values()}
            if len(identities) != len(records):
                raise AccountingError("正例不能重复选择同一原始来源键", 409)
            facts = [self._fact(conn, *context, records[key]) for key in body.positive_record_ids]
            eligibility = {
                "eligible": all(f is not None for f in facts),
                "source_type": "historical",
                "blocked_record_ids": [
                    key for key, fact in zip(body.positive_record_ids, facts) if fact is None
                ],
                "issues": []
                if all(f is not None for f in facts)
                else [
                    "复杂、红字、结转、现金或缺少稳定银行/往来证据的凭证只能保留候选，不能发布规则"
                ],
            }
            identity = uuid4().hex
            stamp = now()
            conn.execute(
                "INSERT INTO migration_learning_candidates(id,project_id,book_id,firm_id,company_id,definition_json,positive_record_ids_json,eligibility_json,reason,request_key,request_fingerprint,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    project_id,
                    book_id,
                    actor.firm_id,
                    project["company_id"],
                    encode(body.definition.model_dump()),
                    encode(body.positive_record_ids),
                    encode(eligibility),
                    body.reason,
                    body.idempotency_key,
                    digest,
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            row = dict(
                conn.execute(
                    "SELECT * FROM migration_learning_candidates WHERE id=?", (identity,)
                ).fetchone()
            )
            for key in body.positive_record_ids:
                self._save_sample(conn, actor, row, context, records[key], "positive")
            self.migration._audit(
                conn,
                actor,
                book_id,
                "historical.candidate.create",
                identity,
                {"reason": body.reason, "source_type": "historical"},
            )
            return self._json(conn, actor, row, book)

    def review(self, actor, book_id, project_id, candidate_id, body):
        body = (
            body
            if isinstance(body, LegacyLearningReview)
            else LegacyLearningReview.model_validate(body)
        )
        digest = fingerprint(body.model_dump())
        self._verify_candidate_sources(actor, book_id, project_id, candidate_id)
        with self.db.transaction(write=True, actor=actor) as conn:
            row, context = self._candidate(conn, actor, book_id, project_id, candidate_id, True)
            if self._prior(
                conn,
                "migration_learning_reviews",
                ("candidate_id", candidate_id),
                body.idempotency_key,
                digest,
            ):
                return self._json(conn, actor, row, context[1])
            self._expect(row, body)
            if row["status"] != "candidate" or row["created_by"] == actor.user_id:
                raise AccountingError("须由另一名会计独立核对尚未审核的历史候选", 403)
            conn.execute(
                "INSERT INTO migration_learning_reviews(id,candidate_id,project_id,book_id,firm_id,company_id,input_version,decision,evidence_confirmed,reason,request_key,request_fingerprint,reviewed_by,created_at) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                (
                    uuid4().hex,
                    candidate_id,
                    project_id,
                    book_id,
                    actor.firm_id,
                    row["company_id"],
                    row["version"],
                    body.decision,
                    body.reason,
                    body.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            return self._json(
                conn,
                actor,
                dict(
                    conn.execute(
                        "SELECT * FROM migration_learning_candidates WHERE id=?", (candidate_id,)
                    ).fetchone()
                ),
                context[1],
            )

    def _evaluate(self, conn, actor, row, context, negative_ids):
        if self.db.is_postgres:
            value = conn.execute(
                "SELECT accounting.migration_learning_evaluate(?,?::jsonb)",
                (row["id"], encode(negative_ids)),
            ).fetchone()[0]
            return json.loads(value) if isinstance(value, str) else value
        d = RuleDefinition.model_validate_json(row["definition_json"])
        ids = json.loads(row["positive_record_ids_json"]) + negative_ids
        records = self._records(conn, row["project_id"], ids)
        outcomes = []
        for key in ids:
            polarity = "positive" if key not in negative_ids else "negative"
            fact = self._fact(conn, *context, records[key])
            eligible = fact is not None
            matched = False
            equal = False
            if fact:
                s = fact["snapshot"]
                matched = (
                    d.conditions.counterparty_id == s["counterparty_id"]
                    and d.conditions.direction == s["payload"]["direction"]
                    and self.learning._identity_valid(conn, actor, row["book_id"], d)
                )
                expected = (
                    [
                        {
                            "account_code": line.account_code,
                            "debit": s["payload"]["amount"] if line.side == "debit" else "0.00",
                            "credit": s["payload"]["amount"] if line.side == "credit" else "0.00",
                            "auxiliary_values": {"counterparty": s["payload"]["counterparty"]}
                            if "counterparty"
                            in json.loads(
                                conn.execute(
                                    "SELECT auxiliary_requirements FROM accounts WHERE book_id=? AND code=?",
                                    (row["book_id"], line.account_code),
                                ).fetchone()[0]
                            )
                            else {},
                        }
                        for line in d.lines
                    ]
                    if matched
                    else []
                )
                equal = matched and canonical_lines(expected) == canonical_lines(
                    fact["final"]["lines"]
                )
            outcomes.append(
                {
                    "record_id": key,
                    "polarity": polarity,
                    "eligible": eligible,
                    "matched": matched,
                    "equal": equal,
                }
            )
        passed = bool(negative_ids) and all(
            item["eligible"]
            and (
                item["matched"] and item["equal"]
                if item["polarity"] == "positive"
                else not item["matched"]
            )
            for item in outcomes
        )
        return {
            "passed": passed,
            "positive_count": len(ids) - len(negative_ids),
            "negative_count": len(negative_ids),
            "outcomes": outcomes,
            "source_type": "historical",
            "writes_current_ledger": False,
        }

    def shadow(self, actor, book_id, project_id, candidate_id, body):
        body = (
            body
            if isinstance(body, LegacyLearningShadow)
            else LegacyLearningShadow.model_validate(body)
        )
        digest = fingerprint(body.model_dump())
        self._verify_candidate_sources(
            actor, book_id, project_id, candidate_id, body.negative_record_ids
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            row, context = self._candidate(conn, actor, book_id, project_id, candidate_id, True)
            prior = self._prior(
                conn,
                "migration_learning_shadows",
                ("candidate_id", candidate_id),
                body.idempotency_key,
                digest,
            )
            if prior:
                return {
                    "id": prior["id"],
                    "passed": bool(prior["passed"]),
                    "report": json.loads(prior["report_json"]),
                    "candidate": self._json(conn, actor, row, context[1]),
                }
            self._expect(row, body)
            if row["status"] != "reviewed" or row["created_by"] == actor.user_id:
                raise AccountingError("回放需要另一会计已独立审核的候选", 403)
            if set(body.negative_record_ids) & set(json.loads(row["positive_record_ids_json"])):
                raise AccountingError("正例和负例不能重叠", 409)
            records = self._records(conn, project_id, body.negative_record_ids)
            for key in body.negative_record_ids:
                self._save_sample(conn, actor, row, context, records[key], "negative")
            report = self._evaluate(conn, actor, row, context, body.negative_record_ids)
            current = self._context(conn, actor, row)
            identity = uuid4().hex
            conn.execute(
                "INSERT INTO migration_learning_shadows(id,candidate_id,project_id,book_id,firm_id,company_id,input_version,negative_record_ids_json,context_fingerprint,report_json,passed,reason,request_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    candidate_id,
                    project_id,
                    book_id,
                    actor.firm_id,
                    row["company_id"],
                    row["version"],
                    encode(body.negative_record_ids),
                    current,
                    encode(report),
                    int(report["passed"]),
                    body.reason,
                    body.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            return {
                "id": identity,
                "passed": report["passed"],
                "report": report,
                "candidate": self._json(conn, actor, row, context[1]),
            }

    def publish(self, actor, book_id, project_id, candidate_id, body):
        body = (
            body
            if isinstance(body, LegacyLearningPublish)
            else LegacyLearningPublish.model_validate(body)
        )
        digest = fingerprint(body.model_dump())
        self._verify_candidate_sources(
            actor, book_id, project_id, candidate_id, shadow_id=body.shadow_id
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            row, context = self._candidate(conn, actor, book_id, project_id, candidate_id, True)
            prior = self._prior(
                conn,
                "migration_learning_rules",
                ("candidate_id", candidate_id),
                body.idempotency_key,
                digest,
            )
            if prior:
                return self._json(conn, actor, row, context[1])
            self._expect(row, body)
            if row["status"] != "reviewed" or row["created_by"] == actor.user_id:
                raise AccountingError("规则须由另一名会计明确发布", 403)
            shadow = conn.execute(
                "SELECT * FROM migration_learning_shadows WHERE id=? AND candidate_id=?",
                (body.shadow_id, candidate_id),
            ).fetchone()
            if (
                not shadow
                or not shadow["passed"]
                or shadow["context_fingerprint"] != self._context(conn, actor, row)
            ):
                raise AccountingError("回放未通过或科目、主数据及资料已改变，须重新回放", 409)
            definition = RuleDefinition.model_validate_json(row["definition_json"])
            self.catalog._validate_template(conn, book_id, definition.as_template())
            if not self.learning._identity_valid(conn, actor, book_id, definition):
                raise AccountingError("交易对方稳定身份已失效，请重新核对", 409)
            existing = conn.execute(
                "SELECT definition_json FROM migration_learning_rules r WHERE book_id=? AND NOT EXISTS(SELECT 1 FROM migration_learning_retirements x WHERE x.rule_id=r.id)",
                (book_id,),
            ).fetchall()
            if any(
                self.learning._overlaps(
                    conn,
                    actor,
                    book_id,
                    definition,
                    RuleDefinition.model_validate_json(r["definition_json"]),
                )
                for r in existing
            ) or self.learning._rule_conflicts(conn, actor, book_id, definition):
                raise AccountingError("已发布规则条件重叠，请先停用或调整候选", 409)
            conn.execute(
                "INSERT INTO migration_learning_rules(id,candidate_id,project_id,book_id,firm_id,company_id,version,definition_json,shadow_id,context_fingerprint,reason,request_key,request_fingerprint,published_by,created_at) VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    candidate_id,
                    project_id,
                    book_id,
                    actor.firm_id,
                    row["company_id"],
                    row["definition_json"],
                    shadow["id"],
                    shadow["context_fingerprint"],
                    body.reason,
                    body.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            return self._json(
                conn,
                actor,
                dict(
                    conn.execute(
                        "SELECT * FROM migration_learning_candidates WHERE id=?", (candidate_id,)
                    ).fetchone()
                ),
                context[1],
            )

    def retire(self, actor, book_id, project_id, candidate_id, body):
        body = (
            body
            if isinstance(body, LegacyLearningRetire)
            else LegacyLearningRetire.model_validate(body)
        )
        digest = fingerprint(body.model_dump())
        with self.db.transaction(write=True, actor=actor) as conn:
            row, context = self._candidate(conn, actor, book_id, project_id, candidate_id, True)
            prior = self._prior(
                conn,
                "migration_learning_retirements",
                ("candidate_id", candidate_id),
                body.idempotency_key,
                digest,
            )
            if prior:
                return self._json(conn, actor, row, context[1])
            self._expect(row, body)
            rule = conn.execute(
                "SELECT id FROM migration_learning_rules WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row["status"] != "published" or not rule:
                raise AccountingError("只能停用当前已发布规则", 409)
            conn.execute(
                "INSERT INTO migration_learning_retirements(id,candidate_id,rule_id,project_id,book_id,firm_id,company_id,reason,request_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    candidate_id,
                    rule["id"],
                    project_id,
                    book_id,
                    actor.firm_id,
                    row["company_id"],
                    body.reason,
                    body.idempotency_key,
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            return self._json(
                conn,
                actor,
                dict(
                    conn.execute(
                        "SELECT * FROM migration_learning_candidates WHERE id=?", (candidate_id,)
                    ).fetchone()
                ),
                context[1],
            )

    def _available_rules(self, conn, actor, book_id):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT * FROM accounting.migration_learning_public_rules(?)", (book_id,)
            ).fetchall()
        book = self.access.book(conn, actor, book_id)
        if not book["permissions"]["edit"]:
            return []
        available = conn.execute(
            "SELECT r.* FROM migration_learning_rules r JOIN migration_projects p ON p.id=r.project_id WHERE r.book_id=? AND r.firm_id=? AND p.status='active' AND NOT EXISTS(SELECT 1 FROM migration_learning_retirements x WHERE x.rule_id=r.id)",
            (book_id, actor.firm_id),
        ).fetchall()
        return [
            r
            for r in available
            if r["context_fingerprint"]
            == self._context(
                conn,
                actor,
                dict(
                    conn.execute(
                        "SELECT * FROM migration_learning_candidates WHERE id=?",
                        (r["candidate_id"],),
                    ).fetchone()
                ),
            )
        ]

    def policy_fingerprint_in_transaction(self, conn, actor, book_id):
        self.access.book(conn, actor, book_id)
        rows = self._available_rules(conn, actor, book_id)
        return fingerprint(
            [
                {
                    "id": r["id"],
                    "version": r["version"],
                    "definition": json.loads(r["definition_json"]),
                }
                for r in sorted(rows, key=lambda r: r["id"])
            ]
        )

    def render_in_transaction(self, conn, actor, book_id, source):
        book = self.access.book(conn, actor, book_id, "edit")
        if (
            book["period_status"] != "open"
            or source["source_kind"] != "bank"
            or source["book_id"] != book_id
        ):
            return None
        rows = self._available_rules(conn, actor, book_id)
        payload = {
            "occurred_on": source["date"],
            "amount": cents_to_money(source["amount_cents"]),
            "direction": source["direction"],
            "payment_status": "paid",
            "counterparty": source["counterparty"],
            "category": "other",
            "project": "",
        }
        snapshot = {
            "source_kind": "bank",
            "business_type": "daily_cashflow",
            "payload": payload,
            "context": {},
            "recognized": {},
        }
        candidates = []
        for row in rows:
            definition = RuleDefinition.model_validate_json(row["definition_json"])
            if self.learning.matches_in_transaction(conn, actor, book_id, definition, snapshot):
                candidates.append((row, definition))
        if not candidates:
            return None
        if len(candidates) > 1:
            raise AccountingError("多个历史企业规则匹配，请会计处理规则冲突", 409)
        row, definition = candidates[0]
        try:
            result = self.catalog._render(
                conn,
                actor,
                book_id,
                definition.as_template(),
                payload,
                source["amount_cents"],
                {},
                {},
            )
        except AccountingError:
            return None
        if result["issues"]:
            return None
        return {
            **result,
            "migration_rule_id": row["id"],
            "migration_rule_version": row["version"],
            "explanation": f"来自独立核对历史样例及正负例回放的企业规则“{definition.name}”；仅生成本账套草稿，仍须会计复核。",
        }
