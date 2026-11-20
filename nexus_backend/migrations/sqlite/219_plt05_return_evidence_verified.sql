-- PLT05-C：区分“人工声明已过账”与“证据已核验”。
-- 文件回执的内容是 CSV（不以 { 开头），本身即客户软件结果；
-- 手工回执的内容是 JSON（以 { 开头），只有补传证据原件后才算核验。
ALTER TABLE bridge_return_imports ADD COLUMN evidence_verified INTEGER NOT NULL DEFAULT 0;

UPDATE bridge_return_imports
SET evidence_verified = 1
WHERE CAST(content AS TEXT) NOT LIKE '{%';
