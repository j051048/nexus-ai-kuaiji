-- PLT05-C：区分“人工声明已过账”与“证据已核验”。
ALTER TABLE accounting.bridge_return_imports
  ADD COLUMN IF NOT EXISTS evidence_verified BOOLEAN NOT NULL DEFAULT false;

UPDATE accounting.bridge_return_imports
SET evidence_verified = true
WHERE convert_from(content, 'UTF8') NOT LIKE '{%';

GRANT UPDATE (evidence_verified) ON accounting.bridge_return_imports TO accounting_app;
