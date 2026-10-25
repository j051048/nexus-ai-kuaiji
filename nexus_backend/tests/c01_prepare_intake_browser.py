"""Create only synthetic in-flight intake state in a V10 disposable SQLite DB.

The employee draft/original/old XML result must already exist through real APIs.
This is not a worker crash simulator, production endpoint or supplier adapter.
No connection/path is accepted from environment or input. No trigger is removed.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounting.database import Database  # noqa: E402
from app.accounting.domain import Actor  # noqa: E402


def prepare(values):
    if os.getenv("C01_BROWSER_INTAKE_FIXTURE") != "1":
        raise ValueError("Explicit isolated browser fixture opt-in required")
    if set(values) != {"draft_id", "attachment_id", "nonce", "kind", "mode"}:
        raise ValueError("Unexpected fixture parameters")
    UUID(values["nonce"])
    if values["kind"] not in {"stale", "running"} or values["mode"] not in {"dev", "preview"}:
        raise ValueError("Unknown isolated fixture state")
    for key in ("draft_id", "attachment_id"):
        if not isinstance(values[key], str) or len(values[key]) != 32:
            raise ValueError("Invalid scoped fixture identity")
        int(values[key], 16)
    root = Path(__file__).resolve().parents[2]
    directory = root / ".codex" / "e2e"
    expected = "C01浏览器恢复-" + values["kind"] + "-" + values["nonce"]
    matches = []
    for path in directory.glob(f"accounting-{values['mode']}-*.db"):
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            continue
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='intake_drafts'"
            ).fetchone()
            row = (
                conn.execute(
                    "SELECT created_by,firm_id,fields_json,status FROM intake_drafts WHERE id=?",
                    (values["draft_id"],),
                ).fetchone()
                if exists
                else None
            )
        if row:
            if (
                row[0] != "local-business-employee"
                or row[1] != "local-firm"
                or row[3] != "draft"
                or json.loads(row[2]).get("description") != expected
            ):
                raise ValueError("Fixture refuses another identity or unmarked business draft")
            matches.append(path)
    if len(matches) != 1:
        raise ValueError("Exactly one V10 isolated draft must match")
    database = Database(matches[0])
    try:
        actor = Actor("local-business-employee", "local-firm")
        with database.transaction(actor=actor) as conn:
            company = conn.execute(
                "SELECT company_id FROM intake_drafts WHERE id=?", (values["draft_id"],)
            ).fetchone()[0]
        with database.transaction(
            write=True, actor=actor, company_id=company, write_scope="company"
        ) as conn:
            draft = conn.execute(
                "SELECT * FROM intake_drafts WHERE id=? AND created_by=? AND firm_id=? AND status='draft'",
                (values["draft_id"], actor.user_id, actor.firm_id),
            ).fetchone()
            if not draft or json.loads(draft["fields_json"])["description"] != expected:
                raise ValueError("Fixture draft changed before setup")
            attachment = conn.execute(
                "SELECT id FROM intake_attachments WHERE id=? AND intake_id=? AND media_type='application/xml'",
                (values["attachment_id"], draft["id"]),
            ).fetchone()
            runs = conn.execute(
                "SELECT * FROM intake_runs WHERE intake_id=?", (draft["id"],)
            ).fetchall()
            if not attachment or len(runs) != 1 or runs[0]["status"] != "succeeded":
                raise ValueError("Real original and one actual XML parse are prerequisites")
            run_id = uuid4().hex
            created = datetime.now(timezone.utc).isoformat()
            # Both states are explicitly synthetic. Keep real chronology: the
            # latest stale run must follow the old successful XML result. Do not
            # backdate it and accidentally make the old suggestion latest.
            conn.execute(
                "INSERT INTO intake_runs(id,intake_id,attachment_id,firm_id,company_id,created_by,input_fingerprint,idempotency_key,status,provider,model,prompt_version,created_at,finished_at,error_code) VALUES(?,?,?,?,?,?,?,?,?,'c01-browser-synthetic-state','no-model','c01-ui-fault-v1',?,?,?)",
                (
                    run_id,
                    draft["id"],
                    attachment["id"],
                    actor.firm_id,
                    company,
                    actor.user_id,
                    hashlib.sha256(values["nonce"].encode()).hexdigest(),
                    "c01-browser-" + values["nonce"],
                    values["kind"],
                    created,
                    created if values["kind"] == "stale" else None,
                    "expired" if values["kind"] == "stale" else "",
                ),
            )
        return {"synthetic_state": True, "worker_restart_proven": False, "run_id": run_id}
    finally:
        database.close()


if __name__ == "__main__":
    try:
        print(json.dumps(prepare(json.load(sys.stdin))))
    except Exception:
        print(
            "Isolated C01 browser state was not prepared; no arbitrary database accepted",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
