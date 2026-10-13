"""Bounded, opt-in resumption under the original execution principal."""
import json
from datetime import datetime, timedelta
from uuid import uuid4

from ..accounting.domain import Actor, AccountingError
from ..accounting.service import fingerprint

RESUMABLE = {"processing_disabled", "ocr_unavailable", "provider_unavailable", "evidence_missing",
             "case_pending", "queue_capacity", "quota_exhausted", "pricing_missing", "policy_invalid"}
STAGES = {"dependency_wait": "识别依赖等待", "eligible_queue_wait": "可执行排队", "claim_lock": "领取与数据库锁等待",
          "model_call": "模型请求", "validation_persistence": "校验与结果落库", "ocr_cache_reuse": "OCR 缓存复用"}


class QueueAutomationService:
    def __init__(self, queue):
        self.queue, self.db, self.access = queue, queue.db, queue.access

    def policy(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id)
            row = conn.execute("SELECT * FROM queue_automation_policies WHERE firm_id=? AND company_id=?", (actor.firm_id, company_id)).fetchone()
            value = dict(row) if row else {"enabled": 0, "allow_verified_supplements": 1, "version": 0, "updated_at": None}
            return {k: bool(value[k]) if k in {"enabled", "allow_verified_supplements"} else value[k]
                    for k in ("enabled", "allow_verified_supplements", "version", "updated_at")} | {
                "permissions": {"manage": self.access.firm_role(conn, actor) in {"owner", "admin"} or self.access.company_role(conn, actor, company_id) in {"owner", "admin"}},
                "resumable_reasons": sorted(RESUMABLE)}

    def update_policy(self, actor, company_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            prior = conn.execute("SELECT version FROM queue_automation_policies WHERE firm_id=? AND company_id=?", (actor.firm_id, company_id)).fetchone()
            if (prior[0] if prior else 0) != request.expected_version:
                raise AccountingError("自动续跑配置已改变，请刷新后再保存", 409)
            stamp = self.queue.database_timestamp(conn)
            conn.execute("INSERT INTO queue_automation_policies(firm_id,company_id,enabled,allow_verified_supplements,version,updated_by,updated_at,last_scanned_at) VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=excluded.enabled,allow_verified_supplements=excluded.allow_verified_supplements,version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                         (actor.firm_id, company_id, int(request.enabled), int(request.allow_verified_supplements), request.expected_version + 1, actor.user_id, stamp))
            self.queue.business._audit(conn, actor, "queue.automation.policy", company_id, {"enabled": request.enabled, "allow_verified_supplements": request.allow_verified_supplements, "reason": request.reason, "version": request.expected_version + 1})
        return self.policy(actor, company_id)

    def _model_plan(self, conn, actor, event):
        """Forecast only new external calls, preserving cached/deterministic work."""
        from ..accounting.catalog import CatalogService
        from ..accounting.models import money_to_cents, cents_to_money
        from ..intelligence.cache import config_key, read_cache
        from ..intelligence.current_evidence import current_attachments
        latest = self.queue.intelligence._latest(conn, event)
        documents, ocr_calls = [], 0
        attachments = current_attachments(self.db, conn, event, conn.execute("SELECT * FROM business_attachments WHERE event_id=? ORDER BY id", (event["id"],)).fetchall())
        for attachment in attachments:
            key = config_key(self.queue.intelligence.provider.settings, attachment["media_type"])
            digest = fingerprint({"attachment_id": attachment["id"], "sha256": attachment["sha256"], "config": key, "fact_version": event["fact_version"], "force_refresh": False})
            run = latest.get(attachment["id"])
            if run and run["status"] == "succeeded" and run["input_fingerprint"] == digest:
                documents.extend(json.loads(run["result_json"])["documents"])
                continue
            cached = read_cache(self.db, conn, actor, event, attachment, key)
            if cached:
                documents.extend(cached.model_dump()["documents"])
            elif not conn.execute("SELECT 1 FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND status IN('queued','running','retry_wait')", (event["id"], attachment["id"])).fetchone():
                ocr_calls += 1
        payload, recognized = json.loads(event["payload"]), {}
        if documents and all(all(d.get(field) is not None for field in ("gross_amount", "net_amount", "tax_amount")) for d in documents):
            gross = sum(money_to_cents(d["gross_amount"]) for d in documents)
            net, tax = (sum(money_to_cents(d[field]) for d in documents) for field in ("net_amount", "tax_amount"))
            if gross == money_to_cents(payload["amount"]) and net + tax == gross:
                recognized = {"net": cents_to_money(net), "tax": cents_to_money(tax)}
        mapping = self.queue.intelligence._mapping(conn, event)
        needs_journal = True
        if mapping:
            context = {"department_id": event["department_id"], "applicant_id": event["applicant_id"]}
            try:
                generated = self.queue.intelligence.ledger._learning_guard().rule_generate_in_transaction(conn, actor, mapping["book_id"], event["template_id"], payload, money_to_cents(payload["amount"]), context, recognized)
                if generated is None:
                    generated = CatalogService(self.db).generate_in_transaction(conn, actor, mapping["book_id"], event["template_id"], payload, money_to_cents(payload["amount"]), context, recognized)
                needs_journal = generated is None
            except AccountingError:
                # The worker also stops at template/rule conflicts; it never asks
                # a model to guess auxiliary values or missing tax evidence.
                needs_journal = False
        return {"ocr": ocr_calls, "proposal": int(needs_journal)}

    def budget_attention(self, conn, actor, event):
        """Read-only gate. The real meter reserves again immediately before send."""
        from ..operations.meter import BudgetError
        meter = self.queue.intelligence._meter()
        policy = conn.execute("SELECT * FROM model_company_policies WHERE firm_id=? AND company_id=?", (event["firm_id"], event["company_id"])).fetchone()
        if not policy or not policy["quota_enabled"]:
            return None
        plan = self._model_plan(conn, actor, event)
        if not any(plan.values()):
            return None
        if conn.execute("SELECT 1 FROM model_costs WHERE firm_id=? AND company_id=? AND overrun=1", (event["firm_id"], event["company_id"])).fetchone():
            return "quota_overrun", BudgetError("quota_overrun").detail
        for kind in ("ocr", "proposal"):
            if plan[kind] and not meter.pricing.for_kind(kind).configured:
                return "pricing_missing", BudgetError("pricing_missing").detail
        reserve = sum(policy[kind + "_reserve_micro"] * count for kind, count in plan.items())
        if policy["monthly_limit_micro"] <= 0 or any(plan[kind] and policy[kind + "_reserve_micro"] <= 0 for kind in plan):
            return "policy_invalid", BudgetError("policy_invalid").detail
        period = meter.period(conn)
        rows = conn.execute("SELECT * FROM model_costs WHERE firm_id=? AND company_id=? AND period=?", (event["firm_id"], event["company_id"], period)).fetchall()
        carry = conn.execute("SELECT COALESCE(SUM(reserved_micro),0) FROM model_costs WHERE firm_id=? AND company_id=? AND period<? AND state IN ('reserved','sent','uncertain')", (event["firm_id"], event["company_id"], period)).fetchone()[0]
        if meter.exposure(rows) + carry + reserve > policy["monthly_limit_micro"]:
            return "quota_exhausted", BudgetError("quota_exhausted").detail
        return None

    def _call_worker(self, conn, name, args):
        conn.execute("SET LOCAL ROLE accounting_worker")
        try:
            return conn.execute("SELECT accounting." + name + "(" + ",".join("?" for _ in args) + ")", args).fetchone()[0]
        finally:
            conn.execute("SET LOCAL ROLE accounting_app")

    def _candidates(self, limit):
        with self.queue.worker_transaction() as conn:
            if self.db.is_postgres:
                return conn.execute("SELECT accounting.worker_claim_resume_candidates(?)", (limit,)).fetchone()[0] or []
            stamp = self.queue.timestamp()
            rows = conn.execute("WITH candidates AS(SELECT w.*,j.execution_user_id,j.firm_id,j.company_id,p.last_scanned_at,row_number() OVER(PARTITION BY j.firm_id,j.company_id ORDER BY w.next_check_at,w.job_id) AS company_rank FROM proposal_resume_watch w JOIN proposal_jobs j ON j.id=w.job_id JOIN queue_automation_policies p ON p.firm_id=j.firm_id AND p.company_id=j.company_id JOIN firm_clients fc ON fc.firm_id=j.firm_id AND fc.company_id=j.company_id WHERE p.enabled=1 AND fc.status='active' AND j.status='needs_attention' AND w.generation=j.generation AND w.next_check_at<=?) SELECT * FROM candidates WHERE company_rank=1 ORDER BY COALESCE(last_scanned_at,''),next_check_at,job_id LIMIT ?", (stamp, limit)).fetchall()
            result, companies = [], set()
            for row in rows:
                pair = (row["firm_id"], row["company_id"])
                if pair in companies:
                    continue
                companies.add(pair)
                token = uuid4().hex
                conn.execute("UPDATE proposal_resume_watch SET claim_token=?,next_check_at=?,check_count=check_count+1 WHERE job_id=?", (token, self.queue.timestamp(60), row["job_id"]))
                conn.execute("UPDATE queue_automation_policies SET last_scanned_at=? WHERE firm_id=? AND company_id=?", (stamp, *pair))
                result.append({"job_id": row["job_id"], "firm_id": row["firm_id"], "company_id": row["company_id"], "execution_user_id": row["execution_user_id"], "generation": row["generation"], "claim_token": token})
                if len(result) == limit:
                    break
            return result

    def _verified_changes(self, conn, event, job, policy, draft):
        if event["fact_version"] == job["expected_fact_version"]:
            return []
        if not policy["allow_verified_supplements"] or event["fact_version"] < job["expected_fact_version"]:
            raise AccountingError("事实版本改变，需人工确认", 409)
        rows = conn.execute("SELECT v.fact_version,v.response_id FROM queue_verified_evidence_versions v JOIN case_responses r ON r.id=v.response_id JOIN case_tasks c ON c.id=r.case_id WHERE v.event_id=? AND v.fact_version>? AND v.fact_version<=? AND v.book_id=? AND v.source_id=? AND v.draft_id=? AND v.draft_version=? AND r.state='accepted' AND r.reviewed_by<>r.created_by AND c.state='closed' AND c.accepted_fact_revision=c.evidence_revision ORDER BY v.fact_version", (event["id"], job["expected_fact_version"], event["fact_version"], job["book_id"], draft["source_id"], job["draft_id"], draft["version"])).fetchall()
        if [row["fact_version"] for row in rows] != list(range(job["expected_fact_version"] + 1, event["fact_version"] + 1)):
            raise AccountingError("事实变化未全部由独立审核补件解释，需人工确认", 409)
        return [row["response_id"] for row in rows]

    def _resume(self, candidate):
        actor = Actor(candidate["execution_user_id"], candidate["firm_id"])
        with self.db.transaction(write=True, actor=actor) as conn:
            job = self.queue.proposals._job(conn, actor, candidate["job_id"])
            watch = conn.execute("SELECT * FROM proposal_resume_watch WHERE job_id=?", (job["id"],)).fetchone()
            policy = conn.execute("SELECT * FROM queue_automation_policies WHERE firm_id=? AND company_id=?", (job["firm_id"], job["company_id"])).fetchone()
            if not watch or not policy or not policy["enabled"] or watch["claim_token"] != candidate["claim_token"] or job["generation"] != candidate["generation"] or job["status"] != "needs_attention" or job["error_code"] not in RESUMABLE:
                return False
            if job["current_run_id"] or conn.execute("SELECT 1 FROM proposal_attempts WHERE job_id=? AND phase='sending'", (job["id"],)).fetchone():
                return False
            event, book, draft = self.queue.proposals._input(conn, actor, job["event_id"], job["book_id"], job["draft_id"])
            if draft["version"] != job["expected_draft_version"] or draft["last_manual_edit_version"] or job["attempt_count"] >= 30:
                return False
            accepted = self._verified_changes(conn, event, job, policy, draft)
            current_policy = self.queue.proposals.policy_fingerprint(conn, event, job["book_id"])
            if current_policy != job["policy_fingerprint"]:
                return False
            if job["error_code"] == "provider_unavailable" and not self.queue.intelligence.provider.settings.suggestion_available:
                return False
            attention = self.queue.proposals._attention(conn, actor, event, draft)
            if attention:
                return False
            config = self.queue.proposals.configuration_fingerprint()
            stamp = self.queue.database_timestamp(conn)
            reason = {"blocker": job["error_code"], "previous_generation": job["generation"], "old_fact_version": job["expected_fact_version"], "new_fact_version": event["fact_version"], "old_draft_version": job["expected_draft_version"], "new_draft_version": draft["version"], "old_configuration": job["configuration_fingerprint"], "new_configuration": config, "accepted_response_ids": accepted, "policy_version": policy["version"]}
            if self.db.is_postgres:
                if not self._call_worker(conn, "worker_resume_proposal", (job["id"], candidate["claim_token"], config, current_policy)):
                    return False
            else:
                conn.execute("INSERT INTO proposal_resume_events(job_id,generation,firm_id,company_id,book_id,event_id,execution_user_id,reason_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (job["id"], job["generation"] + 1, job["firm_id"], job["company_id"], job["book_id"], job["event_id"], actor.user_id, json.dumps(reason, ensure_ascii=False, sort_keys=True), stamp))
                conn.execute("UPDATE proposal_jobs SET status='queued',expected_fact_version=?,configuration_fingerprint=?,generation=generation+1,max_attempts=?,available_at=?,dependency_deadline=?,error_code='',error_detail='',finished_at=NULL,updated_at=? WHERE id=?", (event["fact_version"], config, min(30, job["attempt_count"] + 3), stamp, self.queue.proposals._deadline(stamp), stamp, job["id"]))
            deps = self.queue.proposals._dependencies(conn, actor, event, job["id"], job["generation"] + 1)
            if deps:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.proposal_wait_dependencies(?)", (job["id"],))
                else:
                    conn.execute("UPDATE proposal_jobs SET status='waiting' WHERE id=?", (job["id"],))
            self.queue.business._audit(conn, actor, "queue.proposal.auto_resume", job["id"], reason)
            return True

    def resume_pending(self, limit=12):
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("每轮最多巡检 12 家企业")
        resumed = 0
        for candidate in self._candidates(limit):
            try:
                resumed += int(self._resume(candidate))
            except AccountingError:
                # Revoked principals and stale domain facts never gain a substitute actor.
                continue
        return resumed

    def events(self, actor, book_id, limit=20, offset=0):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "edit")
            rows = conn.execute("SELECT * FROM proposal_resume_events WHERE firm_id=? AND book_id=? ORDER BY created_at DESC,job_id,generation LIMIT ? OFFSET ?", (actor.firm_id, book_id, limit, offset)).fetchall()
            return {"events": [{"job_id": row["job_id"], "generation": row["generation"], "reason": json.loads(row["reason_json"]), "created_at": row["created_at"]} for row in rows]}

    def record_metric(self, conn, job, stage, duration_ms):
        value = max(0, min(86_400_000, int(duration_ms)))
        kind = job.get("job_kind", "ocr")
        if self.db.is_postgres:
            return self._call_worker(conn, "worker_record_job_metric", (kind, job["id"], job["lease_token"], stage, value))
        current = conn.execute("SELECT 1 FROM " + ("proposal_jobs" if kind == "proposal" else "ocr_jobs") + " WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (job["id"], job["lease_token"], self.queue.timestamp())).fetchone()
        if not current:
            return False
        conn.execute("INSERT INTO processing_job_metrics(job_kind,job_id,generation,attempt_no,firm_id,company_id,event_id,book_id,stage,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_kind,job_id,generation,attempt_no,stage) DO NOTHING", (kind, job["id"], job.get("generation", 1), job["attempt_count"], job["firm_id"], job["company_id"], job["event_id"], job.get("book_id"), stage, value, self.queue.database_timestamp(conn)))
        return True

    def metrics(self, actor, company_id, days=7):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            stamp = self.queue.database_timestamp(conn)
            since = (datetime.fromisoformat(stamp) - timedelta(days=days)).isoformat()
            rows = conn.execute("SELECT stage,COUNT(*) AS samples,COALESCE(SUM(duration_ms),0) AS total_ms,COALESCE(AVG(duration_ms),0) AS mean_ms,COALESCE(MAX(duration_ms),0) AS max_ms FROM processing_job_metrics WHERE firm_id=? AND company_id=? AND created_at>=? GROUP BY stage ORDER BY stage", (actor.firm_id, company_id, since)).fetchall()
            return {"company_id": company_id, "days": days, "as_of": stamp, "stages": [{"stage": row["stage"], "label": STAGES[row["stage"]], "samples": row["samples"], "total_ms": row["total_ms"], "mean_ms": round(float(row["mean_ms"]), 1), "max_ms": row["max_ms"]} for row in rows], "notes": "持久统计汇总所有 worker。领取耗时包含连接与锁等待；执行耗时除去模型请求后计入校验、原件准备和落库。事务提交耗时不计入执行段。"}
