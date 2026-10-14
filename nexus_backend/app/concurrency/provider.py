"""Per supplier gates and cross-process transport slots; no credentials in keys."""

from contextlib import contextmanager, closing
from contextvars import ContextVar
import hashlib
import re
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

_scope = ContextVar("accounting_provider_scope", default=None)
_gate_lock = Lock()
_gates = {}


@contextmanager
def provider_scope(db, actor, company_id):
    token = _scope.set((db, actor, company_id))
    try:
        yield
    finally:
        _scope.reset(token)


def supplier_key(base_url):
    # Explicit scenario URLs with the same supplier hostname share transport
    # capacity. API keys, model IDs and path/query contents never become metrics.
    host = (urlsplit(base_url).hostname or "openai-compatible").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", host):
        host = "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]
    return host


def _acquire_process_slot(key, maximum, request_id):
    with _gate_lock:
        active = _gates.setdefault(key, {})
        effective = min([maximum, *active.values()])
        if len(active) >= effective:
            return False
        active[request_id] = maximum
        return True


def _release_process_slot(key, request_id):
    with _gate_lock:
        active = _gates.get(key, {})
        active.pop(request_id, None)
        if not active:
            _gates.pop(key, None)


def _slot(db, actor, company_id, supplier, request_id, lease):
    with closing(db.connect()) as conn:
        try:
            conn.execute("BEGIN" if db.is_postgres else "BEGIN IMMEDIATE")
            if db.is_postgres:
                conn.execute("SET LOCAL ROLE accounting_app")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                conn.execute(
                    "SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)",
                    (actor.user_id, actor.firm_id),
                )
                try:
                    result = conn.execute(
                        "SELECT accounting.acquire_provider_slot(?,?,?,?)",
                        (company_id, supplier, request_id, lease),
                    ).fetchone()[0]
                except Exception as error:
                    if getattr(error, "sqlstate", None) == "42501":
                        from ..intelligence.provider import ProviderError

                        raise ProviderError(
                            "authorization_changed_before_send",
                            "企业授权已改变，未向供应商发送资料",
                            403,
                        ) from None
                    raise
            else:
                from ..accounting.access import AccessControl

                AccessControl().company(conn, actor, company_id)
                conn.execute(
                    "DELETE FROM provider_request_slots WHERE lease_until<=strftime('%s','now')"
                )
                limits = conn.execute(
                    "SELECT * FROM processing_runtime_limits WHERE singleton=1"
                ).fetchone()
                limit = conn.execute(
                    "SELECT max_inflight FROM provider_runtime_limits WHERE provider=?", (supplier,)
                ).fetchone()
                supplier_limit = limit[0] if limit else limits["provider_default_limit"]
                total = conn.execute("SELECT count(*) FROM provider_request_slots").fetchone()[0]
                used = conn.execute(
                    "SELECT count(*) FROM provider_request_slots WHERE provider=?", (supplier,)
                ).fetchone()[0]
                same = conn.execute(
                    "SELECT 1 FROM provider_request_slots WHERE firm_id=? AND company_id=?",
                    (actor.firm_id, company_id),
                ).fetchone()
                result = (
                    total < limits["provider_global_limit"] and used < supplier_limit and not same
                )
                if result:
                    conn.execute(
                        "INSERT INTO provider_request_slots(id,firm_id,company_id,user_id,provider,lease_until) VALUES(?,?,?,?,?,CAST(strftime('%s','now') AS INTEGER)+?)",
                        (request_id, actor.firm_id, company_id, actor.user_id, supplier, lease),
                    )
            conn.commit()
            return bool(result)
        except BaseException:
            conn.rollback()
            raise


def _release(db, actor, request_id):
    with closing(db.connect()) as conn:
        try:
            conn.execute("BEGIN" if db.is_postgres else "BEGIN IMMEDIATE")
            if db.is_postgres:
                conn.execute("SET LOCAL ROLE accounting_app")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                conn.execute(
                    "SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)",
                    (actor.user_id, actor.firm_id),
                )
                conn.execute("SELECT accounting.release_provider_slot(?)", (request_id,))
            else:
                conn.execute(
                    "DELETE FROM provider_request_slots WHERE id=? AND firm_id=? AND user_id=?",
                    (request_id, actor.firm_id, actor.user_id),
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


@contextmanager
def provider_capacity(base_url, maximum, timeout):
    from ..intelligence.provider import ProviderError

    supplier = supplier_key(base_url)
    request_id = uuid4().hex
    if not _acquire_process_slot(supplier, maximum, request_id):
        raise ProviderError("busy", "该供应商处理容量已满，任务稍后重试")
    scope, acquired = _scope.get(), False
    try:
        if scope:
            db, actor, company_id = scope
            acquired = _slot(
                db, actor, company_id, supplier, request_id, min(120, int(timeout) + 20)
            )
            if not acquired:
                raise ProviderError("busy", "企业或供应商处理容量已满，任务稍后重试")
        yield
    finally:
        if acquired:
            try:
                _release(scope[0], scope[1], request_id)
            except Exception:
                # Crash/unknown acknowledgement retains the bounded lease.
                pass
        _release_process_slot(supplier, request_id)
