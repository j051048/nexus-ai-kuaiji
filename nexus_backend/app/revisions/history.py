"""Scoped historical evidence projection; never opens a current business event."""
import json
from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError


class SourceHistoryService:
    def __init__(self, db, store):
        self.db, self.store, self.access = db, store, AccessControl()

    def _history(self, conn, actor, book_id, source_id):
        self.access.book(conn, actor, book_id)
        if self.db.is_postgres:
            row = conn.execute('SELECT accounting.revision_source_history(?,?)', (book_id, source_id)).fetchone()
            value = json.loads(row[0]) if row and row[0] else None
        else:
            row = conn.execute('SELECT * FROM revision_history_snapshots WHERE old_book_id=? AND old_source_id=? AND firm_id=?', (book_id, source_id, actor.firm_id)).fetchone()
            if not row:
                value = None
            else:
                old = json.loads(row['old_snapshot'])
                submitted = json.loads(row['submitted_snapshot'])
                value = {'revision_id': row['revision_id'], 'event_id': row['event_id'], 'book_id': book_id,
                         'source_id': source_id, 'draft_id': row['old_draft_id'], 'fact_version': row['old_fact_version'],
                         'payload': old['payload'], 'submitted_snapshot': submitted,
                         'attachments': [*old.get('attachments', []), *submitted.get('attachment_snapshot', [])],
                         'reason': row['reason'], 'created_at': row['created_at'], 'state': 'superseded'}
        if not value:
            raise AccountingError('来源没有可查看的历史业务快照或无权访问', 404)
        # Exact snapshots can mention the same original twice. Never look up or
        # append attachments uploaded after this historical fact was replaced.
        by_id = {}
        for item in value['attachments']:
            by_id[item['id']] = {key: item[key] for key in ('id', 'filename', 'media_type', 'sha256', 'size_bytes') if key in item}
        value['attachments'] = list(by_id.values())
        # Internal storage metadata must never escape either projection.
        submitted = value['submitted_snapshot']
        if 'attachment_snapshot' in submitted:
            submitted['attachment_snapshot'] = [{k: v for k, v in item.items() if k != 'storage_key'} for item in submitted['attachment_snapshot']]
        return value

    def get(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            return self._history(conn, actor, book_id, source_id)

    def download(self, actor, book_id, source_id, attachment_id):
        with self.db.transaction(actor=actor) as conn:
            history = self._history(conn, actor, book_id, source_id)
            expected = next((item for item in history['attachments'] if item['id'] == attachment_id), None)
            if not expected:
                raise AccountingError('附件不属于该来源的历史事实快照或无权访问', 404)
            if self.db.is_postgres:
                row = conn.execute('SELECT accounting.revision_historical_attachment(?,?,?)', (book_id, source_id, attachment_id)).fetchone()
                metadata = json.loads(row[0]) if row and row[0] else None
            else:
                row = conn.execute('SELECT * FROM business_attachments WHERE id=? AND event_id=? AND firm_id=?', (attachment_id, history['event_id'], actor.firm_id)).fetchone()
                metadata = dict(row) if row else None
            if not metadata or metadata['sha256'] != expected.get('sha256') or metadata['size_bytes'] != expected.get('size_bytes'):
                raise AccountingError('历史附件元数据与留存快照不符，请联系管理员恢复', 503)
            # Read while the repeatable-read authorization scope remains active;
            # AttachmentStore checks path, bounded bytes, exact size and SHA-256.
            data = self.store.read(metadata)
            return data, metadata['filename'], metadata['media_type']
