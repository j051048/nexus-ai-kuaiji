"""Explicit local integration harness; no CLI, worker, publication or HTTP route.

Reuses the rule candidate already inferred by human review. It never guesses
accounts or duplicates LearningService's template/amount/eligibility logic.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlsplit

from .snapshots import SnapshotError, digest, valid_id


class _TransactionView:
    """Let existing public service methods share ONE scoped unit of work.

    No role elevation, SQL rewrite, ignored validation or nested independent commit.
    Only this offline adapter uses the view; production Database remains unchanged.
    """

    def __init__(self, db, conn, actor, writable):
        self.db, self.conn, self.actor, self.writable = db, conn, actor, writable

    def __getattr__(self, key):
        return getattr(self.db, key)

    @contextmanager
    def transaction(self, write=False, actor=None, **kwargs):
        if actor != self.actor or (write and not self.writable) or any(kwargs.values()):
            raise SnapshotError("transaction_scope_mismatch")
        yield self.conn


class LocalRules:
    def __init__(self, db, *, holdout_source_hashes=()):
        if db.is_postgres:
            target = urlsplit(db.path)
            if (
                target.hostname != "127.0.0.1"
                or target.scheme not in {"postgres", "postgresql"}
                or target.port in (None, 5432)
                or not target.path.startswith("/ak01_local_ak05_")
                or target.query
                or target.fragment
            ):
                raise SnapshotError("local_database_required")
        elif not any(
            part in {"tmp_test", "pytest-of-Fei"} or part.startswith("pytest-")
            for part in Path(db.path).resolve().parts
        ):
            raise SnapshotError("isolated_sqlite_required")
        if not isinstance(holdout_source_hashes, tuple) or any(
            not valid_id(s) for s in holdout_source_hashes
        ):
            raise SnapshotError("holdout_source_hashes_invalid")
        self.db = db
        self.holdout_source_hashes = tuple(sorted(set(holdout_source_hashes)))

    @contextmanager
    def _session(self, actor, write=False):
        from app.learning.service import LearningService

        with self.db.transaction(write=write, actor=actor) as conn:
            service = LearningService(_TransactionView(self.db, conn, actor, write))
            yield conn, service

    def _prepare(
        self, conn, service, actor, book_id, source_rule_id, positives, negatives, holdout_ids
    ):
        from app.accounting.review_policy import ReviewPolicyService
        from app.learning.models import RuleDefinition
        from app.learning.service import canonical_lines

        for ids in (positives, negatives):
            if (
                type(ids) is not list
                or not 1 <= len(ids) <= 100
                or any(type(s) is not str or not s for s in ids)
                or len(set(ids)) != len(ids)
            ):
                raise SnapshotError("sample_ids_invalid")
        if set(positives) & set(negatives) or set(positives + negatives) & set(holdout_ids):
            raise SnapshotError("sample_or_holdout_overlap")
        book = service._book(conn, actor, book_id, True)
        source_rule = service.get_rule(actor, book_id, source_rule_id)
        if not set(positives).issubset(source_rule["sample_ids"]):
            raise SnapshotError("source_rule_sample_binding")
        definition = deepcopy(source_rule["definition"])
        if (
            definition["source_kind"] != "business"
            or definition["conditions"]["business_type"] != "procurement_expense"
            or definition["conditions"]["category"] != "office"
        ):
            raise SnapshotError("unsupported_rule_domain")
        # This first batch accepts only already-inferred two-line gross formulas.
        if len(definition["lines"]) != 2 or any(
            line["amount_field"] != "gross" for line in definition["lines"]
        ):
            raise SnapshotError("unsupported_amount_formula")
        identity = service.resolve_counterparty_in_transaction(
            conn, actor, book_id, definition["conditions"]["counterparty"]
        )
        if identity["status"] != "resolved":
            raise SnapshotError("stable_counterparty_required")
        definition["conditions"]["counterparty_id"] = identity["entity_id"]
        typed = RuleDefinition.model_validate(definition)
        bindings, sources = {}, set(self.holdout_source_hashes)
        for sid in holdout_ids:
            held = service.get_sample(actor, book_id, sid)
            sources.add(held["snapshot"]["source"]["fingerprint"])
            for row in conn.execute(
                "SELECT a.sha256 FROM business_attachments a JOIN business_mappings m ON m.event_id=a.event_id WHERE m.draft_id=? AND m.book_id=?",
                (held["draft_id"], book_id),
            ):
                sources.add(row["sha256"])
        for sid in positives + negatives:
            sample = service.get_sample(actor, book_id, sid)
            from app.learning.evidence import reviewed_source, RuleEvidenceError

            try:
                binding = reviewed_source(service, conn, actor, book, sample)
            except RuleEvidenceError as error:
                raise SnapshotError(error.code) from None
            source, event = binding["source"], binding["event"]
            attachments, receipt = binding["attachments"], binding["receipt"]
            if sid in positives and not sample["corrected"]:
                raise SnapshotError("reviewed_correction_required")
            hashes = {a["sha256"] for a in attachments} | {source["fingerprint"]}
            if not hashes or sources & hashes:
                raise SnapshotError("original_source_overlap_or_missing")
            sources |= hashes
            if sid in positives:
                rendered = service._render_sample(conn, actor, book_id, typed, sample["snapshot"])
                if rendered["issues"] or canonical_lines(
                    service._public_lines(rendered)
                ) != canonical_lines(sample["final"]["lines"]):
                    raise SnapshotError("template_not_reviewed_result")
            bindings[sid] = {
                "sample": sample,
                "source": source,
                "event": dict(event),
                "attachments": attachments,
                "receipt": receipt,
            }
        policy = ReviewPolicyService.policy_in_transaction(conn, book_id)
        catalog = service.catalog.get(actor, book_id)
        state = {
            "samples": bindings,
            "catalog": catalog,
            "master": service._master_fingerprint(conn, actor, book_id),
            "policy": policy,
            "source_rule": source_rule,
        }
        from tools.ocr_replay.replay import sha256_path

        implementation = Path(__file__).resolve().parents[3]
        implementation_paths = [
            "tools/ai_inference_lab/rsi/rules.py",
            "nexus_backend/app/learning/service.py",
            "nexus_backend/app/learning/models.py",
            "nexus_backend/app/learning/governance.py",
            "nexus_backend/app/accounting/review_policy.py",
            "nexus_backend/app/accounting/access.py",
            "nexus_backend/app/accounting/catalog.py",
        ]
        artifact = {
            "schema_version": "rsi-enterprise-rule-v1",
            "firm_id": actor.firm_id,
            "company_id": book["company_id"],
            "book_id": book_id,
            "period": book["period"],
            "source_rule_id": source_rule_id,
            "positive_ids": sorted(positives),
            "negative_ids": sorted(negatives),
            "holdout_ids": sorted(holdout_ids),
            "holdout_source_hashes": list(self.holdout_source_hashes),
            "definition": typed.model_dump(),
            "implementation": {p: sha256_path(implementation / p) for p in implementation_paths},
            "state_sha256": digest(state),
            "sample_bindings": {sid: digest(value) for sid, value in bindings.items()},
            "model_verified": False,
            "publication_authorized": False,
        }
        return {**artifact, "artifact_sha256": digest(artifact)}

    def prepare(
        self, actor, book_id, source_rule_id, positive_ids, negative_ids, *, holdout_ids=()
    ):
        with self._session(actor) as (conn, service):
            return self._prepare(
                conn,
                service,
                actor,
                book_id,
                source_rule_id,
                positive_ids,
                negative_ids,
                holdout_ids,
            )

    def _check(self, conn, service, actor, book_id, artifact):
        if not isinstance(artifact, dict) or digest(
            {k: v for k, v in artifact.items() if k != "artifact_sha256"}
        ) != artifact.get("artifact_sha256"):
            raise SnapshotError("artifact_digest")
        if artifact.get("firm_id") != actor.firm_id or artifact.get("book_id") != book_id:
            raise SnapshotError("artifact_scope")
        current = self._prepare(
            conn,
            service,
            actor,
            book_id,
            artifact["source_rule_id"],
            artifact["positive_ids"],
            artifact["negative_ids"],
            artifact["holdout_ids"],
        )
        if current != artifact:
            raise SnapshotError("artifact_stale")

    def stage(self, actor, book_id, artifact):
        from app.learning.models import RuleCreate
        from app.learning.governance import semantic_fingerprint

        with self._session(actor, True) as (conn, service):
            self._check(conn, service, actor, book_id, artifact)
            existing = service._rule_conflicts(
                conn,
                actor,
                book_id,
                RuleCreate(
                    definition=artifact["definition"],
                    sample_ids=artifact["positive_ids"],
                    reason="RSI local",
                ).definition,
                published_only=False,
            )
            for match in existing:
                if match["same_semantics"]:
                    rule = service.get_rule(actor, book_id, match["rule_id"])
                    if (
                        rule["status"] != "draft"
                        or sorted(rule["sample_ids"]) != artifact["positive_ids"]
                    ):
                        raise SnapshotError("existing_rule_requires_explicit_review")
                    assert semantic_fingerprint(rule["definition"]) == semantic_fingerprint(
                        artifact["definition"]
                    )
                    return rule
            return service.save_rule(
                actor,
                book_id,
                RuleCreate(
                    definition=artifact["definition"],
                    sample_ids=artifact["positive_ids"],
                    reason="RSI 本地人工提交候选 " + artifact["artifact_sha256"] + "；不授权发布",
                ),
            )

    def evaluate(self, actor, book_id, artifact, rule_id):
        from app.learning.models import RuleEvaluate
        from app.learning.governance import semantic_fingerprint

        with self._session(actor, True) as (conn, service):
            self._check(conn, service, actor, book_id, artifact)
            rule = service.get_rule(actor, book_id, rule_id)
            if (
                rule["status"] != "draft"
                or sorted(rule["sample_ids"]) != artifact["positive_ids"]
                or semantic_fingerprint(rule["definition"])
                != semantic_fingerprint(artifact["definition"])
            ):
                raise SnapshotError("rule_artifact_binding")
            return service.evaluate(
                actor,
                book_id,
                rule_id,
                RuleEvaluate(
                    expected_version=rule["current_version"],
                    negative_sample_ids=artifact["negative_ids"],
                ),
            )

    def shadow(self, actor, book_id, artifact, current_amount):
        from app.accounting.models import money_to_cents
        from app.learning.models import RuleDefinition

        with self._session(actor) as (conn, service):
            self._check(conn, service, actor, book_id, artifact)
            sample = service.get_sample(actor, book_id, artifact["positive_ids"][0])
            snapshot = deepcopy(sample["snapshot"])
            money_to_cents(current_amount)
            snapshot["payload"]["amount"] = current_amount
            return service._render_sample(
                conn,
                actor,
                book_id,
                RuleDefinition.model_validate(artifact["definition"]),
                snapshot,
            )
