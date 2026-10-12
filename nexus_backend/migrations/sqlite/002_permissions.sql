ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX idx_users_email ON users(lower(email)) WHERE email<>'';
ALTER TABLE firms ADD COLUMN created_by TEXT REFERENCES users(id);
CREATE TABLE companies (id TEXT PRIMARY KEY,name TEXT NOT NULL,tax_id TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX idx_companies_tax_id ON companies(tax_id) WHERE tax_id<>'';
CREATE TABLE firm_clients (
 firm_id TEXT NOT NULL REFERENCES firms(id),company_id TEXT NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),PRIMARY KEY(firm_id,company_id)
);
INSERT INTO companies(id,name) SELECT 'legacy-company-' || id,name FROM books;
INSERT INTO firm_clients(firm_id,company_id) SELECT firm_id,'legacy-company-' || id FROM books;
CREATE TABLE books_upgrade (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),company_id TEXT NOT NULL,name TEXT NOT NULL,
 period TEXT NOT NULL,UNIQUE(id,firm_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
INSERT INTO books_upgrade(id,firm_id,company_id,name,period) SELECT id,firm_id,'legacy-company-' || id,name,period FROM books;
DROP TABLE books;
ALTER TABLE books_upgrade RENAME TO books;
CREATE TABLE memberships_upgrade (
 firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','admin','operator','reviewer','viewer')),PRIMARY KEY(firm_id,user_id)
);
INSERT INTO memberships_upgrade SELECT * FROM memberships;
DROP TABLE memberships;
ALTER TABLE memberships_upgrade RENAME TO memberships;
CREATE TABLE book_access_upgrade (
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL DEFAULT 'reviewer' CHECK(role IN ('editor','reviewer','viewer')),PRIMARY KEY(book_id,user_id),
 FOREIGN KEY(book_id,firm_id) REFERENCES books(id,firm_id)
);
INSERT INTO book_access_upgrade(book_id,firm_id,user_id) SELECT book_id,firm_id,user_id FROM book_access;
DROP TABLE book_access;
ALTER TABLE book_access_upgrade RENAME TO book_access;
CREATE TABLE departments (
 id TEXT PRIMARY KEY,company_id TEXT NOT NULL REFERENCES companies(id),name TEXT NOT NULL,UNIQUE(id,company_id)
);
CREATE TABLE company_memberships (
 company_id TEXT NOT NULL REFERENCES companies(id),user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','admin','finance','employee')),department_id TEXT,
 PRIMARY KEY(company_id,user_id),FOREIGN KEY(department_id,company_id) REFERENCES departments(id,company_id)
);
CREATE INDEX idx_book_access_user ON book_access(user_id,firm_id);
CREATE INDEX idx_books_company ON books(firm_id,company_id);
CREATE INDEX idx_company_memberships_user ON company_memberships(user_id,company_id);
