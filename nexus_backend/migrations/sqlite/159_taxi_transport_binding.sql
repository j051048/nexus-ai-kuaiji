-- Forward-only transport snapshot. Never infer identity for pre-existing batches.
ALTER TABLE tax_fee_batches ADD COLUMN transport_snapshot_json TEXT NOT NULL DEFAULT '[]';
CREATE TRIGGER IF NOT EXISTS taxi_transport_snapshot_immutable
 BEFORE UPDATE OF transport_snapshot_json ON tax_fee_batches
 WHEN NEW.transport_snapshot_json<>OLD.transport_snapshot_json
 BEGIN SELECT RAISE(ABORT,'Taxi transport snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS taxi_transport_snapshot_required
 BEFORE UPDATE OF status ON drafts
 WHEN NEW.status IN('approved','exported') AND NEW.status<>OLD.status AND EXISTS(
  SELECT 1 FROM tax_fee_batches b WHERE b.draft_id=NEW.id AND b.book_id=NEW.book_id
   AND json_array_length(b.transport_snapshot_json)=0)
 BEGIN SELECT RAISE(ABORT,'Taxi transport snapshot required'); END;
