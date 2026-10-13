"""Test-only historical restoration; never imported by production code.

Legacy freeze tests need an already-closed period despite intentionally broken
or unreviewed facts. That state is restored by the dedicated administrator,
not by weakening the new production month-close workflow.
"""
import os
from contextlib import contextmanager

from app.accounting.database import WRITE_LOCK


@contextmanager
def _sqlite_conn(db, conn):
    if conn is not None:
        yield conn
    else:
        with db.transaction(write=True) as current:
            yield current


def restore_period_state(db, book_id, status='closed', conn=None):
    if status not in {'open', 'closed'}:
        raise ValueError('fixture period state must be open or closed')
    if db.is_postgres:
        import psycopg
        admin_url = os.getenv('ACCOUNTING_TEST_POSTGRES_URL')
        if not admin_url:
            raise RuntimeError('historical fixture requires explicit dedicated test administrator DSN')
        with psycopg.connect(admin_url) as admin:
            if not admin.execute('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()[0]:
                raise RuntimeError('fixture restoration requires the dedicated administrator')
            admin.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
            admin.execute('UPDATE accounting.periods SET status=%s WHERE book_id=%s', (status, book_id))
        return {'status': status}
    with _sqlite_conn(db, conn) as current:
        trigger = current.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='period_reviewed_close'").fetchone()
        if trigger:
            current.execute('DROP TRIGGER period_reviewed_close')
        try:
            current.execute('UPDATE periods SET status=? WHERE book_id=?', (status, book_id))
        finally:
            if trigger:
                current.execute(trigger[0])
    return {'status': status}


def complete_month_close(accounting, actor, book_id):
    """Explicit synthetic matching receipts, then the actual close workflow."""
    import json
    from uuid import uuid4
    from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
    from app.closing.service import ClosingService
    from app.delivery.models import ReceiptCreate
    from app.delivery.service import DeliveryService

    delivery = DeliveryService(accounting.db)
    for export in accounting.list_exports(actor, book_id)['exports']:
        body = json.loads(delivery.template(actor, book_id, export['id']))
        for item in body['items']:
            item['status'] = 'accepted'
            item['external_voucher_id'] = 'fixture-external-' + item['draft_id']
        delivery.create_receipt(actor, book_id, export['id'], ReceiptCreate(**body, idempotency_key=uuid4().hex, reason='测试专用合成回执，并未连接客户软件'))
    service = ClosingService(accounting.db)
    snapshot = service.preflight(actor, book_id, PreflightRequest(idempotency_key=uuid4().hex))
    assert snapshot['report']['passed'], snapshot['report']
    service.review(actor, book_id, snapshot['id'], SnapshotReview(reason='测试专用本期资料完整性确认及当前快照复核', materials_confirmed=True))
    return service.close(actor, book_id, CloseRequest(snapshot_id=snapshot['id'], reason='测试专用当前月结确认'))
