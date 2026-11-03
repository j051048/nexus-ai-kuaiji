"""Seed only a fresh, local P1 browser database with synthetic failure evidence."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]

from app.accounting.database import Database  # noqa: E402
from app.accounting.directory import DirectoryService  # noqa: E402
from app.accounting.models import BookCreate, CompanyCreate  # noqa: E402
from app.accounting.seed import LOCAL_ACTOR, bootstrap  # noqa: E402
from app.accounting.service import AccountingService  # noqa: E402
from test_next04_handoff_adapters import failed_batch, unknown_notification  # noqa: E402


def main():
    path = Path(os.environ["ACCOUNTING_DB_PATH"]).resolve()
    allowed = (ROOT / "tmp_test/next-p1").resolve()
    if (
        not path.is_relative_to(allowed)
        or path.exists()
        or os.environ.get("ACCOUNTING_AUTH_MODE") != "local"
        or os.environ.get("ACCOUNTING_DATABASE_URL")
    ):
        raise SystemExit("Only a fresh isolated local P1 database is allowed")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)
    try:
        ledger = AccountingService(db)
        bootstrap(ledger, seed_examples=False)
        company = DirectoryService(db).create_company(
            LOCAL_ACTOR, CompanyCreate(name="接手验证企业（虚构）")
        )
        book = ledger.create_book(
            LOCAL_ACTOR, BookCreate(name="接手验证账套", company_id=company["id"], period="2026-10")
        )
        case = SimpleNamespace(db=db, manager=LOCAL_ACTOR, company=company, book=book)
        failed_batch(case)
        unknown_notification(case)
        with db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute("UPDATE batch_operation_items SET result_json='{}' WHERE id='item-n04'")
    finally:
        db.close()


if __name__ == "__main__":
    main()
