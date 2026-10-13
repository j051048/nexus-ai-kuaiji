import hashlib
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import now


class ClosingService:
    def __init__(self, db, accounting=None):
        self.db, self.accounting, self.access = db, accounting, AccessControl()

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute('INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)',
                     (actor.firm_id, book_id, actor.user_id, action, object_id, json.dumps(details, ensure_ascii=False, sort_keys=True), now()))

    def _current(self, conn, actor, book_id):
        self.access.book(conn, actor, book_id, 'review')
        if self.db.is_postgres:
            row = conn.execute('SELECT accounting.closing_fingerprint(?) AS fingerprint,accounting.closing_report(?) AS report', (book_id, book_id)).fetchone()
            return row['fingerprint'], row['report'], None
        inputs = conn.execute('SELECT inputs_json FROM closing_fact_basis WHERE book_id=?', (book_id,)).fetchone()['inputs_json']
        counts = {row['category']: row['count'] for row in conn.execute('SELECT category,count(*) AS count FROM closing_issue_rows WHERE book_id=? GROUP BY category', (book_id,))}
        issues = [dict(row) for row in conn.execute('SELECT category,object_type,object_id,detail FROM closing_issue_rows WHERE book_id=? ORDER BY category,object_type,object_id LIMIT 500', (book_id,))]
        total = sum(counts.values())
        return hashlib.sha256(inputs.encode()).hexdigest(), {'engine_version': 1, 'passed': total == 0, 'issue_count': total, 'truncated': total > 500, 'counts': counts, 'issues': issues}, inputs

    @staticmethod
    def _json(conn, row, actor, current_fingerprint, period_status):
        review = conn.execute('SELECT reason,materials_confirmed,created_at FROM closing_reviews WHERE snapshot_id=? AND book_id=? AND reviewed_by=?', (row['id'], row['book_id'], actor.user_id)).fetchone()
        return {key: row[key] for key in ('id', 'book_id', 'period', 'input_fingerprint', 'created_by', 'created_at')} | {
            'report': json.loads(row['report_json']), 'stale': row['input_fingerprint'] != current_fingerprint,
            'historical': period_status == 'closed', 'reviewed_by_me': bool(review), 'review': dict(review) if review else None}

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            digest, report, _ = self._current(conn, actor, book_id)
            snapshots = [self._json(conn, row, actor, digest, book['period_status']) for row in conn.execute('SELECT * FROM closing_snapshots WHERE book_id=? AND firm_id=? ORDER BY created_at DESC,id DESC LIMIT 20', (book_id, actor.firm_id))]
            receipt = conn.execute('SELECT * FROM closing_receipts WHERE book_id=?', (book_id,)).fetchone()
            return {'book_id': book_id, 'book_name': book['name'], 'company_id': book['company_id'], 'period': book['period'], 'period_status': book['period_status'],
                    'current_fingerprint': digest, 'current_report': report, 'snapshots': snapshots, 'receipt': dict(receipt) if receipt else None,
                    'coverage_note': '预检只能核对已进入系统的资料；会计复核时须明确确认本期资料与银行流水完整性。'}

    def _snapshot(self, conn, actor, book_id, snapshot_id):
        self.access.book(conn, actor, book_id, 'review')
        row = conn.execute('SELECT * FROM closing_snapshots WHERE id=? AND book_id=? AND firm_id=?', (snapshot_id, book_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError('月结预检快照不存在或无权访问', 404)
        return row

    def get_snapshot(self, actor, book_id, snapshot_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            row = self._snapshot(conn, actor, book_id, snapshot_id)
            digest, _, _ = self._current(conn, actor, book_id)
            return self._json(conn, row, actor, digest, book['period_status'])

    def preflight(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            if book['period_status'] != 'open':
                raise AccountingError('已关闭期间仅可查看历史月结快照', 403)
            prior = conn.execute('SELECT * FROM closing_snapshots WHERE firm_id=? AND created_by=? AND request_key=?', (actor.firm_id, actor.user_id, request.idempotency_key)).fetchone()
            if prior and prior['book_id'] != book_id:
                raise AccountingError('预检幂等键已用于另一账套', 409)
            digest, report, inputs = self._current(conn, actor, book_id)
            if prior:
                return self._json(conn, prior, actor, digest, book['period_status'])
            if self.db.is_postgres:
                snapshot_id = conn.execute('SELECT accounting.create_closing_snapshot(?,?)', (book_id, request.idempotency_key)).fetchone()[0]
            else:
                snapshot_id = uuid4().hex
                conn.execute('INSERT INTO closing_snapshots(id,firm_id,company_id,book_id,period,input_fingerprint,inputs_json,report_json,request_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                             (snapshot_id, actor.firm_id, book['company_id'], book_id, book['period'], digest, inputs, json.dumps(report, ensure_ascii=False, sort_keys=True), request.idempotency_key, actor.user_id, now()))
            self._audit(conn, actor, book_id, 'closing.preflight', snapshot_id, {'input_fingerprint': digest, 'passed': report['passed'], 'issue_count': report['issue_count']})
            return self._json(conn, self._snapshot(conn, actor, book_id, snapshot_id), actor, digest, book['period_status'])

    def review(self, actor, book_id, snapshot_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            row = self._snapshot(conn, actor, book_id, snapshot_id)
            digest, report, _ = self._current(conn, actor, book_id)
            if book['period_status'] != 'open' or digest != row['input_fingerprint'] or report != json.loads(row['report_json']) or not report['passed']:
                raise AccountingError('预检存在阻断或事实已变化，请处理后重新生成快照', 409)
            previous = conn.execute('SELECT * FROM closing_reviews WHERE snapshot_id=? AND reviewed_by=?', (snapshot_id, actor.user_id)).fetchone()
            if previous:
                if previous['reason'] != request.reason:
                    raise AccountingError('此月结快照已按不同依据复核，不能覆盖历史', 409)
            else:
                if self.db.is_postgres:
                    conn.execute('SELECT accounting.review_closing_snapshot(?,?,?,?)', (book_id, snapshot_id, request.reason, request.materials_confirmed))
                else:
                    conn.execute('INSERT INTO closing_reviews VALUES(?,?,?,?,1,?)', (snapshot_id, book_id, actor.user_id, request.reason, now()))
                self._audit(conn, actor, book_id, 'closing.snapshot.review', snapshot_id, {'input_fingerprint': digest, 'reason': request.reason, 'materials_confirmed': True})
            return self._json(conn, row, actor, digest, book['period_status'])

    def close(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, 'review')
            row = self._snapshot(conn, actor, book_id, request.snapshot_id)
            prior = conn.execute('SELECT * FROM closing_receipts WHERE book_id=?', (book_id,)).fetchone()
            if prior:
                if prior['snapshot_id'] != request.snapshot_id or prior['closed_by'] != actor.user_id or prior['reason'] != request.reason:
                    raise AccountingError('本期已依据另一月结快照或操作关闭', 409)
                return {'book_id': book_id, 'period': book['period'], 'status': 'closed', 'snapshot_id': request.snapshot_id, 'replayed': True}
            digest, report, _ = self._current(conn, actor, book_id)
            reviewed = conn.execute('SELECT 1 FROM closing_reviews WHERE snapshot_id=? AND book_id=? AND reviewed_by=?', (request.snapshot_id, book_id, actor.user_id)).fetchone()
            if book['period_status'] != 'open' or not reviewed or not report['passed'] or report != json.loads(row['report_json']) or digest != row['input_fingerprint']:
                raise AccountingError('关期必须使用当前通过预检、明确确认资料完整性并经本人复核的快照', 409)
            if self.db.is_postgres:
                conn.execute('SELECT accounting.close_with_snapshot(?,?,?)', (book_id, request.snapshot_id, request.reason))
            else:
                conn.execute('INSERT INTO closing_receipts VALUES(?,?,?,?,?,?)', (book_id, book['period'], request.snapshot_id, actor.user_id, request.reason, now()))
                conn.execute("UPDATE periods SET status='closed' WHERE book_id=? AND period=? AND status='open'", (book_id, book['period']))
                self._audit(conn, actor, book_id, 'period.close', book['period'], {'snapshot_id': request.snapshot_id, 'input_fingerprint': digest, 'reason': request.reason})
            return {'book_id': book_id, 'period': book['period'], 'status': 'closed', 'snapshot_id': request.snapshot_id, 'replayed': False}
