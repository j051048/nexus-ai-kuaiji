-- OPT07-D: account roles are policy data, not account-code literals.
CREATE TABLE IF NOT EXISTS account_roles (
  book_id TEXT NOT NULL,
  code TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('bank','payable','receivable')),
  PRIMARY KEY(book_id,code,role)
);

INSERT OR IGNORE INTO account_roles(book_id,code,role)
SELECT book_id,code,'bank' FROM accounts WHERE code='1002';
INSERT OR IGNORE INTO account_roles(book_id,code,role)
SELECT book_id,code,'payable' FROM accounts WHERE code IN ('2202','2241');
INSERT OR IGNORE INTO account_roles(book_id,code,role)
SELECT book_id,code,'receivable' FROM accounts WHERE code IN ('1122','1221');
