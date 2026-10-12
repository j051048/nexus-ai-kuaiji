CREATE TABLE IF NOT EXISTS firms (id TEXT PRIMARY KEY,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS memberships (
 firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('operator','reviewer')),PRIMARY KEY(firm_id,user_id)
);
CREATE TABLE IF NOT EXISTS books (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),name TEXT NOT NULL,
 period TEXT NOT NULL,UNIQUE(id,firm_id)
);
CREATE TABLE IF NOT EXISTS book_access (
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,user_id TEXT NOT NULL,PRIMARY KEY(book_id,user_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES books(id,firm_id),
 FOREIGN KEY(firm_id,user_id) REFERENCES memberships(firm_id,user_id)
);
CREATE TABLE IF NOT EXISTS periods (
 book_id TEXT NOT NULL REFERENCES books(id),period TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),PRIMARY KEY(book_id,period)
);
CREATE TABLE IF NOT EXISTS accounts (
 book_id TEXT NOT NULL REFERENCES books(id),code TEXT NOT NULL,name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(book_id,code)
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),external_id TEXT NOT NULL,
 source_name TEXT NOT NULL,date TEXT NOT NULL,description TEXT NOT NULL,counterparty TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0),direction TEXT NOT NULL CHECK(direction IN ('expense','income')),
 evidence_ref TEXT NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(book_id,external_id),UNIQUE(id,book_id)
);
CREATE TABLE IF NOT EXISTS drafts (
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),source_id TEXT NOT NULL UNIQUE,
 description TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','exported')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),explanation TEXT NOT NULL,
 approved_by TEXT REFERENCES users(id),approved_at TEXT,exported_at TEXT,
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),UNIQUE(id,book_id)
);
CREATE TABLE IF NOT EXISTS source_evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL,book_id TEXT NOT NULL,
 evidence_ref TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE TABLE IF NOT EXISTS draft_lines (
 draft_id TEXT NOT NULL,book_id TEXT NOT NULL,ordinal INTEGER NOT NULL,account_code TEXT NOT NULL,
 debit_cents INTEGER NOT NULL CHECK(debit_cents>=0),credit_cents INTEGER NOT NULL CHECK(credit_cents>=0),
 PRIMARY KEY(draft_id,ordinal),FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id),
 FOREIGN KEY(book_id,account_code) REFERENCES accounts(book_id,code)
);
CREATE TABLE IF NOT EXISTS exports (
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),idempotency_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,csv TEXT NOT NULL,count INTEGER NOT NULL,filename TEXT NOT NULL,
 created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),UNIQUE(book_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,firm_id TEXT NOT NULL REFERENCES firms(id),book_id TEXT REFERENCES books(id),
 user_id TEXT NOT NULL REFERENCES users(id),action TEXT NOT NULL,object_id TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_book ON sources(book_id,date);
CREATE INDEX IF NOT EXISTS idx_drafts_book ON drafts(book_id,status);
CREATE INDEX IF NOT EXISTS idx_audit_book ON audit_events(book_id,id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON source_evidence(book_id,source_id,id);
