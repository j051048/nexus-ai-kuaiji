-- compatibility: incremental
ALTER TABLE ingestion_runs ADD COLUMN event_key TEXT NOT NULL DEFAULT '';
ALTER TABLE ingestion_runs ADD COLUMN raw_event_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ingestion_runs ADD COLUMN request_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX idx_ingestion_runs_event
 ON ingestion_runs(connection_id,event_key) WHERE event_key<>'';
