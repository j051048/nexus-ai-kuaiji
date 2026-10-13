"""Immutable sample sets and read-only upgrade checks against recorded evidence."""

import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint, now
from ..business.service import encode
from ..accounting.queries import page_bounds

STATUSES = ("passed", "different", "uncovered", "stale")
JOURNAL_MODES = {"rules", "templates", "stored_model"}


def _scope(mode):
    if not isinstance(mode, str):
        return None
    return (
        "journal" if mode in JOURNAL_MODES else "invoice_fields" if mode == "stored_ocr" else None
    )


def _indexed_report(run, blockers, label):
    report, settings = run.get("report", {}), run.get("settings", {})
    items = report.get("items", [])
    if (
        not isinstance(items, list)
        or report.get("mode") != settings.get("mode")
        or _scope(settings.get("mode")) is None
    ):
        blockers.append(label + "报告结构或评测范围无效")
        return {}
    indexed = {}
    for item in items:
        key = item.get("standard_id") if isinstance(item, dict) else None
        if not isinstance(key, str) or not key or key in indexed:
            blockers.append(label + "报告包含缺失或重复标准标识")
            continue
        state, differences = item.get("status"), item.get("differences")
        if (
            state not in STATUSES
            or not isinstance(differences, list)
            or state == "passed"
            and differences
            or state == "different"
            and not differences
        ):
            blockers.append(label + "报告状态与差异证据不一致")
            item = {**item, "status": "uncovered", "differences": []}
        indexed[key] = item
    actual_counts = {
        state: sum(item["status"] == state for item in indexed.values()) for state in STATUSES
    }
    reported = report.get("counts")
    if (
        not isinstance(reported, dict)
        or set(reported) != set(STATUSES)
        or any(type(value) is not int or value < 0 for value in reported.values())
        or reported != actual_counts
    ):
        blockers.append(label + "报告汇总与逐项结果不一致")
    return indexed


def compare_reports(
    baseline, candidate, expected_ids, *, minimum_samples=1, allowed_differences=0, stale=False
):
    """Coverage, scope and evidence validity are gates; missing is never a pass."""
    blockers = []
    left = _indexed_report(baseline, blockers, "基线")
    right = _indexed_report(candidate, blockers, "候选")
    expected = set(expected_ids)
    if set(left) != expected or set(right) != expected:
        blockers.append("两次运行必须覆盖该版本固定样本集的全部标准")
    domain = _scope(baseline.get("settings", {}).get("mode"))
    if domain != _scope(candidate.get("settings", {}).get("mode")):
        blockers.append("运行范围不同，不能将字段识别与分录结果直接比较")
    if len(expected) < minimum_samples:
        blockers.append("已确认样本数不足最低要求")
    if stale:
        blockers.append("样本原件或业务事实已变化，请建立新版本")
    if candidate.get("stale"):
        blockers.append("候选运行的配置或证据已变化，请按当前配置重新评测")
    engine = baseline.get("report", {}).get("engine_version")
    candidate_engine = candidate.get("report", {}).get("engine_version")
    if (
        type(engine) is not int
        or engine < 1
        or type(candidate_engine) is not int
        or engine != candidate_engine
    ):
        blockers.append("评测引擎版本不同，须先使用统一引擎重新建立可比报告")
    if baseline.get("id") and baseline["id"] == candidate.get("id"):
        blockers.append("基线与候选为同一运行，不能作为升级比较")
    items = []
    for key in sorted(expected):
        old, new = left.get(key), right.get(key)
        old_status = old["status"] if old else "uncovered"
        new_status = new["status"] if new else "uncovered"
        items.append(
            {
                "standard_id": key,
                "baseline_status": old_status,
                "candidate_status": new_status,
                "regressed": old_status == "passed" and new_status != "passed",
                "differences": new.get("differences", []) if new else [],
            }
        )
    counts = {
        status: sum(item["candidate_status"] == status for item in items) for status in STATUSES
    }
    regressions = sum(item["regressed"] for item in items)
    if counts["uncovered"] or counts["stale"]:
        blockers.append("候选版本仍有未覆盖或失效样本")
    if counts["different"] > allowed_differences:
        blockers.append("候选版本差异项超过允许数量")
    if regressions:
        blockers.append("候选版本存在原本一致而现在不一致的退步项")
    return {
        "eligible": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "sample_count": len(expected),
        "counts": counts,
        "regressions": regressions,
        "items": items,
        "domain": domain,
        "baseline_archived": bool(baseline.get("stale")),
        "policy": {"minimum_samples": minimum_samples, "allowed_differences": allowed_differences},
        "scope_note": "比较已归档结果，没有现场运行新模型。旧配置基线保留历史结果，候选必须仍有效；只适用于固定样本已测范围，实际发布须管理员确认。",
    }


class BenchmarkService:
    def __init__(self, quality):
        self.quality, self.db = quality, quality.db

    def _row(self, conn, actor, book_id, benchmark_id):
        self.quality.access.book(conn, actor, book_id, "review")
        row = conn.execute(
            "SELECT * FROM quality_benchmarks WHERE id=? AND book_id=? AND firm_id=?",
            (benchmark_id, book_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("固定样本集不存在或无权访问", 404)
        return row

    def _json(self, conn, actor, row):
        ids = json.loads(row["standard_ids_json"])
        current = all(
            self.quality._current(
                conn, actor, self.quality._standard(conn, actor, row["book_id"], key)
            )
            for key in ids
        )
        return {
            key: row[key]
            for key in ("id", "name", "version", "book_id", "reason", "created_by", "created_at")
        } | {"standard_ids": ids, "sample_count": len(ids), "stale": not current}

    def create(self, actor, book_id, request):
        digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.quality.access.book(conn, actor, book_id, "review")
            prior = conn.execute(
                "SELECT * FROM quality_benchmarks WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["book_id"] != book_id or prior["request_fingerprint"] != digest:
                    raise AccountingError("样本集幂等键已用于不同内容", 409)
                return self._json(conn, actor, prior)
            sample_ids = set()
            for key in request.standard_ids:
                standard = self.quality._standard(conn, actor, book_id, key)
                if standard["sample_id"] in sample_ids:
                    raise AccountingError(
                        "同一审核样例只能选择一份标准，重复标签版本不能增加样本数", 409
                    )
                sample_ids.add(standard["sample_id"])
                if (
                    not self.quality._current(conn, actor, standard)
                    or not conn.execute(
                        "SELECT 1 FROM quality_confirmations WHERE standard_id=? AND book_id=?",
                        (key, book_id),
                    ).fetchone()
                ):
                    raise AccountingError("固定样本集只能使用仍有效且已独立确认的标准", 409)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM quality_benchmarks WHERE book_id=? AND name=?",
                (book_id, request.name),
            ).fetchone()[0]
            key = uuid4().hex
            conn.execute(
                "INSERT INTO quality_benchmarks(id,firm_id,company_id,book_id,name,version,standard_ids_json,reason,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.name,
                    version,
                    encode(request.standard_ids),
                    request.reason,
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            return self._json(conn, actor, self._row(conn, actor, book_id, key))

    def list(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            self.quality.access.book(conn, actor, book_id, "review")
            total = conn.execute(
                "SELECT COUNT(*) FROM quality_benchmarks WHERE book_id=? AND firm_id=?",
                (book_id, actor.firm_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM quality_benchmarks WHERE book_id=? AND firm_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (book_id, actor.firm_id, limit, offset),
            ).fetchall()
            return {
                "benchmarks": [self._json(conn, actor, row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def get(self, actor, book_id, benchmark_id):
        with self.db.transaction(actor=actor) as conn:
            return self._json(conn, actor, self._row(conn, actor, book_id, benchmark_id))

    def _costs(self, conn, actor, book_id, run):
        ids = set()
        for item in run["report"]["items"]:
            provenance = item.get("provenance", {})
            ids.update(provenance.get("run_ids", []))
            if provenance.get("run_id"):
                ids.add(provenance["run_id"])
        if not ids:
            mode = run["settings"]["mode"]
            deterministic = mode in {"rules", "templates"}
            return {
                "observed_calls": 0,
                "known_micro_cny": 0 if deterministic else None,
                "unknown_calls": 0,
                "measurement": "deterministic_replay_no_model_call"
                if deterministic
                else "no_model_cost_evidence",
            }
        # Cost rows have their own stricter manager ACL. Do not disclose company
        # budgets to a reviewer who is only assigned one accounting book.
        from ..operations.service import OperationsService

        book = self.quality.access.book(conn, actor, book_id, "review")
        try:
            OperationsService(self.db, None)._company(conn, actor, book["company_id"], manage=True)
        except AccountingError:
            return {
                "observed_calls": None,
                "known_micro_cny": None,
                "unknown_calls": None,
                "measurement": "not_authorized",
            }
        from ..intelligence.structured import STRUCTURED_PROVIDER, STRUCTURED_VERSION

        runs = conn.execute(
            "SELECT id,provider,prompt_version,cache_hit FROM ai_runs WHERE firm_id=? AND company_id=? AND id IN ("
            + ",".join("?" for _ in ids)
            + ")",
            (actor.firm_id, book["company_id"], *sorted(ids)),
        ).fetchall()
        # Local parse and reuse runs carry explicit no-send evidence. Original
        # supplier fees for reused content are outside this request's scope.
        free = {
            row["id"]
            for row in runs
            if row["cache_hit"]
            or row["provider"] == STRUCTURED_PROVIDER
            and row["prompt_version"] == STRUCTURED_VERSION
        }
        external = ids - free
        records = (
            conn.execute(
                "SELECT id,state,estimated_micro,confirmed_micro FROM model_costs WHERE firm_id=? AND company_id=? AND id IN ("
                + ",".join("?" for _ in external)
                + ")",
                (actor.firm_id, book["company_id"], *sorted(external)),
            ).fetchall()
            if external
            else []
        )
        known = [
            0
            if row["state"] == "released"
            else row["confirmed_micro"]
            if row["confirmed_micro"] is not None
            else row["estimated_micro"]
            for row in records
        ]
        known.extend(0 for _ in free)
        confirmed = [
            0 if row["state"] == "released" else row["confirmed_micro"]
            for row in records
            if row["state"] == "released" or row["confirmed_micro"] is not None
        ]
        estimated = [
            row["estimated_micro"]
            for row in records
            if row["confirmed_micro"] is None
            and row["estimated_micro"] is not None
            and row["state"] != "released"
        ]
        unknown = sum(value is None for value in known) + len(external) - len(records)
        return {
            "observed_calls": len(external),
            "processing_records": len(ids),
            "no_send_records": len(free),
            "known_micro_cny": sum(value for value in known if value is not None)
            if any(value is not None for value in known)
            else None,
            "unknown_calls": unknown,
            "measurement": "recorded_model_calls_reused_no_new_charge",
            "confirmed_micro_cny": sum(confirmed) if confirmed else None,
            "estimated_micro_cny": sum(estimated) if estimated else None,
            "pending_calls": sum(
                row["state"] in {"reserved", "sent", "uncertain"} for row in records
            ),
            "complete": unknown == 0
            and not any(row["state"] in {"reserved", "sent", "uncertain"} for row in records),
            "scope_note": "费用来自该范围归档请求，估算与确认分列；缺费用证据或未核定请求不视为零，本次比较没有新增模型调用。",
        }

    def compare(self, actor, book_id, benchmark_id, request):
        with self.db.transaction(actor=actor) as conn:
            benchmark = self._json(conn, actor, self._row(conn, actor, book_id, benchmark_id))
            runs = []
            for key in (request.baseline_run_id, request.candidate_run_id):
                row = conn.execute(
                    "SELECT * FROM quality_runs WHERE id=? AND book_id=? AND firm_id=?",
                    (key, book_id, actor.firm_id),
                ).fetchone()
                if not row:
                    raise AccountingError("比较运行不存在或无权访问", 404)
                runs.append(self.quality._run_json(conn, actor, row))
            report = compare_reports(
                *runs,
                benchmark["standard_ids"],
                minimum_samples=request.minimum_samples,
                allowed_differences=request.allowed_differences,
                stale=benchmark["stale"],
            )
            return {
                "benchmark": benchmark,
                "baseline": {
                    "id": runs[0]["id"],
                    "settings": runs[0]["settings"],
                    "stale": runs[0]["stale"],
                    "costs": self._costs(conn, actor, book_id, runs[0]),
                },
                "candidate": {
                    "id": runs[1]["id"],
                    "settings": runs[1]["settings"],
                    "stale": runs[1]["stale"],
                    "costs": self._costs(conn, actor, book_id, runs[1]),
                },
                "report": report,
            }
