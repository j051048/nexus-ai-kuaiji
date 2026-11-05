-- compatibility: incremental
CREATE TABLE IF NOT EXISTS client_message_reads (
 message_id TEXT NOT NULL REFERENCES client_messages(id),
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 user_id TEXT NOT NULL REFERENCES users(id),
 read_at TEXT NOT NULL,
 PRIMARY KEY(message_id,user_id)
);
