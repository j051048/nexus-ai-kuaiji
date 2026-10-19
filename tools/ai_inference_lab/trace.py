"""DL-R1C trace contract: state machine, trusted run context, authorization projection."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from html import escape

SCHEMA = "dl-trace-v1"
ROLES = {"employee", "company_owner", "accountant", "admin"}
OUTCOMES = {"completed", "review_required", "blocked", "failed", "unknown"}
EVENT_TYPES = (
    "run_created", "extraction_started", "extraction_completed", "validation_completed",
    "review_requested", "review_started", "review_completed", "blocked", "failed",
    "unknown", "run_finished",
)
TERMINAL = {"blocked", "failed", "unknown"}


class TraceError(ValueError):
    def __init__(self, code, message="invalid trace"):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class RunContext:
    run_id: str
    firm_id: str
    company_id: str
    ledger_id: str
    book_id: str
    sample_id: str
    employee_id: str
    input_version: str
    policy_version: str


@dataclass(frozen=True)
class EvidenceRef:
    firm_id: str
    company_id: str
    sample_id: str
    input_version: str
    page: int
    field: str
    quote: str


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    seq: int
    event_type: str
    outcome: str = ""
    step_id: str = ""
    attempt_id: str = ""
    firm_id: str = ""
    company_id: str = ""
    ledger_id: str = ""
    book_id: str = ""
    sample_id: str = ""
    employee_id: str = ""
    input_version: str = ""
    policy_version: str = ""
    reason_code: str = ""
    message: str = ""
    evidence: tuple[EvidenceRef, ...] = ()
    field_diff: tuple[dict, ...] = ()
    usage: dict | None = None
    cost: dict | None = None
    timestamp: str = ""


@dataclass(frozen=True)
class Principal:
    role: str
    firm_ids: frozenset[str]
    company_ids: frozenset[str]
    employee_id: str = ""
    can_diagnose: bool = False


def _event_identity(event, context):
    for key in ("firm_id", "company_id", "ledger_id", "book_id", "sample_id", "employee_id"):
        if getattr(event, key) != getattr(context, key):
            raise TraceError("IDENTITY_DRIFT")
    if event.input_version != context.input_version or event.policy_version != context.policy_version:
        raise TraceError("VERSION_DRIFT")


def _validate_evidence(event, context):
    for ref in event.evidence:
        if not isinstance(ref, EvidenceRef):
            raise TraceError("EVIDENCE_SHAPE")
        if (ref.firm_id, ref.company_id, ref.sample_id, ref.input_version) != (
            context.firm_id, context.company_id, context.sample_id, context.input_version
        ):
            raise TraceError("EVIDENCE_SCOPE")
        if type(ref.page) is not int or ref.page < 1 or not ref.field or not ref.quote:
            raise TraceError("EVIDENCE_SHAPE")


def _validate_run(events, context):
    seen_ids = {}
    seen_seq = set()
    state = "new"
    outcome = ""
    terminal_event = None
    by_type = {}
    for event in sorted(events, key=lambda e: e.seq):
        if event.event_type not in EVENT_TYPES:
            raise TraceError("UNKNOWN_EVENT")
        sig = json.dumps(event.__dict__, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o), sort_keys=True)
        if event.event_id in seen_ids:
            if seen_ids[event.event_id] != sig:
                raise TraceError("EVENT_ID_CONFLICT")
            continue
        seen_ids[event.event_id] = sig
        if event.seq in seen_seq:
            raise TraceError("DUPLICATE_SEQ")
        seen_seq.add(event.seq)
        _event_identity(event, context)
        _validate_evidence(event, context)
        type_ = event.event_type
        if type_ == "run_created":
            if state != "new":
                raise TraceError("DUPLICATE_RUN_CREATED")
            state = "created"
        elif type_ == "extraction_started":
            if state != "created":
                raise TraceError("ILLEGAL_TRANSITION")
            state = "extracting"
        elif type_ == "extraction_completed":
            if state != "extracting":
                raise TraceError("ILLEGAL_TRANSITION")
            state = "extracted"
        elif type_ == "validation_completed":
            if state not in {"extracted", "review_completed"}:
                raise TraceError("ILLEGAL_TRANSITION")
            state = "validated"
            outcome = event.outcome
        elif type_ == "review_requested":
            if state != "validated" or outcome not in {"review_required", "completed"}:
                raise TraceError("ILLEGAL_TRANSITION")
            state = "review_requested"
        elif type_ == "review_started":
            if state != "review_requested":
                raise TraceError("ILLEGAL_TRANSITION")
            state = "reviewing"
        elif type_ == "review_completed":
            if state != "reviewing":
                raise TraceError("ILLEGAL_TRANSITION")
            state = "review_completed"
            outcome = event.outcome
        elif type_ in TERMINAL:
            if state in {"new", "created"}:
                raise TraceError("ILLEGAL_TRANSITION")
            state = type_
            outcome = type_
            terminal_event = event
        elif type_ == "run_finished":
            if state in {"new", "created", "extracting"}:
                raise TraceError("ILLEGAL_TRANSITION")
            if not event.outcome or event.outcome not in OUTCOMES:
                raise TraceError("MISSING_OUTCOME")
            if terminal_event and event.outcome != terminal_event.outcome:
                raise TraceError("OUTCOME_OVERWRITE")
            if state == "run_finished":
                raise TraceError("DUPLICATE_FINISH")
            state = "run_finished"
            outcome = event.outcome
        by_type[type_] = event
    if state == "run_finished" and "run_created" not in by_type:
        raise TraceError("MISSING_RUN_CREATED")
    return outcome


def validate_events(events, contexts):
    """Validate per-run sequences and immutable identities; returns errors."""
    grouped = {}
    for event in events:
        if not isinstance(event, Event):
            return ["EVENT_SHAPE"]
        grouped.setdefault(event.run_id, []).append(event)
    for run_id, rows in grouped.items():
        context = contexts.get(run_id)
        if context is None:
            return ["MISSING_RUN_CONTEXT"]
        try:
            _validate_run(rows, context)
        except TraceError as exc:
            return [exc.code]
    return []


def _visible(event, context, principal):
    if principal.role not in ROLES:
        raise TraceError("UNKNOWN_ROLE")
    if context.firm_id not in principal.firm_ids or context.company_id not in principal.company_ids:
        return False
    if principal.role == "employee" and context.employee_id != principal.employee_id:
        return False
    return True


def project(events, contexts, principal):
    if principal.role not in ROLES:
        raise TraceError("UNKNOWN_ROLE")
    runs = {}
    errors = []
    for run_id, rows in {e.run_id: [] for e in events}.items():
        rows = [e for e in events if e.run_id == run_id]
        context = contexts.get(run_id)
        if context is None:
            continue
        if not _visible(rows[0], context, principal):
            continue
        try:
            outcome = _validate_run(rows, context)
        except TraceError as exc:
            errors.append({"run_id": "visible-run", "code": exc.code})
            continue
        visible_events = []
        for event in sorted(rows, key=lambda e: e.seq):
            item = {
                "event_id": event.event_id, "seq": event.seq, "event_type": event.event_type,
                "outcome": event.outcome, "reason_code": event.reason_code,
                "message": event.message, "timestamp": event.timestamp,
                "evidence": [
                    {"page": e.page, "field": e.field, "quote": e.quote}
                    for e in event.evidence
                ],
                "field_diff": list(event.field_diff),
            }
            if principal.can_diagnose:
                if event.usage is not None:
                    item["usage"] = event.usage
                if event.cost is not None:
                    item["cost"] = event.cost
                item["diagnostic"] = {"attempt_id": event.attempt_id, "policy_version": event.policy_version}
            visible_events.append(item)
        runs[run_id] = {"outcome": outcome, "events": visible_events}
    return {"role": principal.role, "company_ids": sorted(principal.company_ids), "runs": runs, "errors": errors}


# ---------- DL-R1B result -> events ----------

def events_from_dl02(report, contexts, base_seq=1000):
    """Convert actual DL-R1B result rows into validated events."""
    events = []
    seq = base_seq
    for result in report.get("results", []):
        ctx = contexts.get(result["sample_id"])
        if ctx is None:
            continue
        run_id = ctx.run_id
        status = result.get("candidate_status", "unknown")
        anomalies = result.get("anomalies_after") or result.get("anomalies_before") or []
        evidence = []
        for ref in result.get("evidence", []):
            if not isinstance(ref, dict):
                continue
            field, page, quote = ref.get("field"), ref.get("page"), ref.get("quote")
            if field in ("gross_amount", "invoice_number", "issued_on") and isinstance(page, int) and isinstance(quote, str):
                evidence.append(EvidenceRef(ctx.firm_id, ctx.company_id, ctx.sample_id, ctx.input_version, page, field, quote))
        def add(type_, outcome="", reason="", message="", diff=(), ev=()):
            nonlocal seq
            seq += 1
            events.append(Event(
                event_id=f"{run_id}-{seq}", run_id=run_id, seq=seq, event_type=type_,
                outcome=outcome, firm_id=ctx.firm_id, company_id=ctx.company_id,
                ledger_id=ctx.ledger_id, book_id=ctx.book_id, sample_id=ctx.sample_id,
                employee_id=ctx.employee_id, input_version=ctx.input_version,
                policy_version=ctx.policy_version, reason_code=reason, message=message,
                evidence=tuple(ev), field_diff=tuple(diff),
                usage={"known": False}, cost={"known": False},
                timestamp="2026-10-19T00:00:00Z",
            ))
        add("run_created")
        add("extraction_started")
        add("extraction_completed", ev=evidence)
        add("validation_completed", outcome="review_required" if anomalies else "completed", reason=",".join(anomalies))
        if result.get("route") == "blocked":
            add("blocked", outcome="blocked", reason=result.get("route_reason") or "BLOCKED")
        elif result.get("review_requested"):
            add("review_requested", reason=result.get("review_status", ""), message="conditonal review")
            if result.get("review_attempted"):
                add("review_started")
                if result.get("review_completed"):
                    add("review_completed", outcome="review_required")
                elif result.get("review_status") == "unknown":
                    add("unknown", outcome="unknown", reason="UNKNOWN")
                else:
                    add("failed", outcome="failed", reason=result.get("review_status", "FAILED"))
        add("run_finished", outcome=status, reason=result.get("route_reason", ""), diff=tuple(
            {"field": k, "before": result.get("before", {}).get(k), "after": result.get("after", {}).get(k)}
            for k in set(result.get("before", {})) | set(result.get("after", {}))
        ))
    return events


# ---------- viewer ----------

def write_viewer(path: Path, projection):
    path = Path(path)
    payload = json.dumps(projection, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>衡账 · 离线实验</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f7f7f5;color:#17202a}}main{{max-width:1000px;margin:auto;padding:1rem}}
header{{border-bottom:2px solid #17202a;padding:.8rem 0}}.badge{{border:1px solid #17202a;border-radius:3px;padding:.1rem .3rem;font-size:.8rem}}
.run{{background:#fff;border:1px solid #c9d1d9;margin:1rem 0;padding:1rem}}.muted{{color:#4a5568}}
button{{font:inherit;padding:.45rem .7rem;margin:.2rem;border:1px solid #17202a;background:#fff;cursor:pointer}}
button:focus-visible,a:focus-visible{{outline:3px solid #b45309}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
@media(max-width:600px){{main{{padding:.5rem}}.run{{padding:.7rem}}}}
</style></head><body><main>
<header><span class="badge">离线实验 · 非生产</span><h1>处理进度与异常</h1>
<p class="muted">仅展示当前合成角色获准的数据；不执行审核、记账、导出或重试。</p></header>
<div role="group" aria-label="按状态筛选">
<button data-f="all">全部</button><button data-f="review_required">待人工</button>
<button data-f="blocked">阻断</button><button data-f="unknown">未知</button><button data-f="completed">完成</button></div>
<div id="app"></div></main>
<script>const data={payload};const app=document.getElementById('app');
function render(filter){{
 app.textContent='';
 const entries=Object.entries(data.runs);let shown=0;
 if(!entries.length){{const p=document.createElement('p');p.textContent='当前角色没有获准的运行。';app.append(p);return}}
 for(const [runId,run] of entries){{
  if(filter!=='all'&&run.outcome!==filter)continue;
  shown++;
  const box=document.createElement('section');box.className='run';
  const h=document.createElement('h2');h.textContent='处理进度 · '+runId;box.append(h);
  const summary=document.createElement('p');summary.className='muted';summary.textContent='当前状态：'+run.outcome;box.append(summary);
  for(const e of run.events){{
   const p=document.createElement('p');const b=document.createElement('span');b.className='badge';b.textContent=e.event_type+' · '+(e.outcome||'-');
   const t=document.createElement('span');t.textContent=' '+(e.message||'')+(e.reason_code?' · '+e.reason_code:'');p.append(b,t);box.append(p);
   for(const ev of e.evidence){{const li=document.createElement('div');li.textContent='证据 第'+ev.page+'页 '+ev.field+'：'+ev.quote;box.append(li)}}
   for(const d of e.field_diff){{const li=document.createElement('div');li.textContent='字段 '+d.field+'：'+String(d.before)+' → '+String(d.after);box.append(li)}}
   if(e.diagnostic){{const pre=document.createElement('pre');pre.textContent=JSON.stringify(e.diagnostic,null,2);box.append(pre)}}
  }}
  app.append(box);
 }}
 if(!shown){{const p=document.createElement('p');p.textContent='没有符合条件的运行。';app.append(p)}}
}}
document.querySelectorAll('button[data-f]').forEach(b=>b.addEventListener('click',()=>render(b.dataset.f)));
render('all');
</script></body></html>"""
    path.write_text(html, encoding="utf-8")
    return path
