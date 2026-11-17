-- OPT07-D: account roles are policy data, not account-code literals.
CREATE TABLE IF NOT EXISTS accounting.account_roles (
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  code TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('bank','payable','receivable')),
  PRIMARY KEY(book_id,code,role)
);

INSERT INTO accounting.account_roles(book_id,code,role)
SELECT a.book_id,a.code,v.role
FROM accounting.accounts a
JOIN (VALUES ('1002','bank'),('2202','payable'),('2241','payable'),
             ('1122','receivable'),('1221','receivable')) AS v(code,role)
  ON a.code=v.code
ON CONFLICT DO NOTHING;

ALTER TABLE accounting.account_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.account_roles FORCE ROW LEVEL SECURITY;
CREATE POLICY account_roles_company_read ON accounting.account_roles
  FOR SELECT USING (EXISTS(
    SELECT 1 FROM accounting.books b
    WHERE b.id=book_id AND accounting.company_visible(b.company_id)
  ));
CREATE POLICY account_roles_company_write ON accounting.account_roles
  FOR ALL USING (EXISTS(
    SELECT 1 FROM accounting.books b
    WHERE b.id=book_id AND accounting.company_visible(b.company_id)
  )) WITH CHECK (EXISTS(
    SELECT 1 FROM accounting.books b
    WHERE b.id=book_id AND accounting.company_visible(b.company_id)
  ));
GRANT SELECT,INSERT,DELETE ON accounting.account_roles TO accounting_app;
