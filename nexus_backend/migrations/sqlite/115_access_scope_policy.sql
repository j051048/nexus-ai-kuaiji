-- compatibility: incremental

CREATE TABLE staff_access_assignments(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 template TEXT NOT NULL CHECK(template IN('assistant','accountant','reviewer','auditor')),
 company_id TEXT REFERENCES companies(id),valid_from TEXT,valid_to TEXT,
 state TEXT NOT NULL CHECK(state IN('active','revoked')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from<valid_to)
);
CREATE INDEX idx_staff_scope_user ON staff_access_assignments(firm_id,user_id,state);
CREATE TABLE delivery_contact_access(
 book_id TEXT NOT NULL REFERENCES books(id),user_id TEXT NOT NULL REFERENCES users(id),firm_id TEXT NOT NULL REFERENCES firms(id),company_id TEXT NOT NULL REFERENCES companies(id),
 valid_to TEXT,state TEXT NOT NULL CHECK(state IN('active','revoked')),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,PRIMARY KEY(book_id,user_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE VIEW effective_book_access AS SELECT book_id,firm_id,user_id,CASE MAX(rank) WHEN 3 THEN 'reviewer' WHEN 2 THEN 'editor' ELSE 'viewer' END AS role FROM (
 SELECT a.book_id,a.firm_id,a.user_id,CASE a.role WHEN 'reviewer' THEN 3 WHEN 'editor' THEN 2 ELSE 1 END AS rank FROM book_access a
 UNION ALL
 SELECT b.id,b.firm_id,g.user_id,CASE g.template WHEN 'reviewer' THEN 3 WHEN 'auditor' THEN 1 ELSE 2 END
 FROM staff_access_assignments g JOIN books b ON b.firm_id=g.firm_id AND (g.company_id IS NULL OR g.company_id=b.company_id)
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN users u ON u.id=g.user_id AND u.active=1
 JOIN memberships m ON m.user_id=g.user_id AND m.firm_id=g.firm_id
 WHERE g.state='active' AND (g.valid_from IS NULL OR julianday(g.valid_from)<=julianday('now')) AND (g.valid_to IS NULL OR julianday(g.valid_to)>julianday('now'))
 AND (g.template='auditor' AND m.role IN('viewer','operator','reviewer') OR g.template IN('assistant','accountant') AND m.role IN('operator','reviewer') OR g.template='reviewer' AND m.role='reviewer')
) grants GROUP BY book_id,firm_id,user_id;
DROP VIEW service_book_user_permissions;
CREATE VIEW service_book_user_permissions AS SELECT b.id AS book_id,u.id AS user_id,
 (m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))) AS can_edit,
 (m.role IN('owner','admin') OR ba.role='reviewer' AND (m.role='reviewer')) AS can_review,
 (cm.user_id IS NOT NULL) AS can_contact
 FROM books b JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN users u ON u.active=1 LEFT JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN effective_book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id;
DROP VIEW monthly_delivery_eligible_contacts;
CREATE VIEW monthly_delivery_eligible_contacts AS SELECT b.id AS book_id,u.id AS user_id FROM books b
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN service_plan_versions p ON p.book_id=b.id AND p.version=(SELECT MAX(version) FROM service_plan_versions WHERE book_id=b.id)
 JOIN users u ON u.id=p.contact_user_id AND u.active=1
 JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN effective_book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE m.role IN('owner','admin') OR (ba.user_id IS NOT NULL AND (m.role IS NOT NULL OR cm.role IN('owner','admin','finance'))) OR EXISTS(SELECT 1 FROM delivery_contact_access g WHERE g.book_id=b.id AND g.user_id=u.id AND g.state='active' AND (g.valid_to IS NULL OR julianday(g.valid_to)>julianday('now')));

CREATE TRIGGER agency_book_grant BEFORE INSERT ON book_access WHEN NEW.role='reviewer' AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.user_id AND role IN('owner','admin','reviewer')) BEGIN SELECT RAISE(ABORT,'Formal reviewer must be authorized firm staff'); END;
CREATE TRIGGER agency_book_grant_update BEFORE UPDATE ON book_access WHEN NEW.role='reviewer' AND NOT EXISTS(SELECT 1 FROM memberships WHERE firm_id=NEW.firm_id AND user_id=NEW.user_id AND role IN('owner','admin','reviewer')) BEGIN SELECT RAISE(ABORT,'Formal reviewer must be authorized firm staff'); END;
