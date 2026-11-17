-- OPT03-B follow-up: allow only the one-time database hash initialization.
--
-- SQLite writes audit rows in two steps: the insert leaves prev_hash/row_hash
-- NULL, then audit_events_chain_insert fills them. The original guard also
-- recomputed the canonical hash and could reject a legitimate insert when the
-- caller supplied JSON details (for example the material-action audit trigger)
-- because JSON text canonicalisation differed between the two trigger bodies.
--
-- The one-time transition remains restricted: content columns are immutable,
-- prev_hash must equal the current firm chain tail, and row_hash must become
-- non-NULL. Hash correctness is checked independently by
-- scripts/verify_audit_chain.py. No row may be updated twice.
DROP TRIGGER IF EXISTS audit_events_append_only_update;
CREATE TRIGGER audit_events_append_only_update
BEFORE UPDATE ON audit_events
WHEN NOT (
    OLD.row_hash IS NULL
    AND NEW.row_hash IS NOT NULL
    AND NEW.id = OLD.id
    AND NEW.firm_id = OLD.firm_id
    AND NEW.book_id IS OLD.book_id
    AND NEW.company_id IS OLD.company_id
    AND NEW.user_id = OLD.user_id
    AND NEW.action = OLD.action
    AND NEW.object_id IS OLD.object_id
    AND NEW.details IS OLD.details
    AND NEW.created_at = OLD.created_at
    AND NEW.prev_hash = COALESCE(
        (
            SELECT row_hash
            FROM audit_events
            WHERE firm_id = OLD.firm_id AND id < OLD.id
            ORDER BY id DESC
            LIMIT 1
        ),
        ''
    )
)
BEGIN
    SELECT RAISE(ABORT, 'audit_events are append-only');
END;
