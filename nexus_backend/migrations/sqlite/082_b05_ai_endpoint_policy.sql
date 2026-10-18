-- compatibility: incremental
-- B05: trusted AI endpoint directory and versioned company egress policy.
-- Existing env-configured deployments remain legacy until an operator creates
-- a managed policy; no existing company is silently disabled or expanded.

CREATE TABLE ai_trusted_endpoints(
 id TEXT PRIMARY KEY,
 protocol TEXT NOT NULL CHECK(protocol IN('https','http')),
 host TEXT NOT NULL, port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
 path_prefix TEXT NOT NULL DEFAULT '',
 service_identity TEXT NOT NULL CHECK(length(trim(service_identity)) BETWEEN 1 AND 120),
 allow_http INTEGER NOT NULL DEFAULT 0 CHECK(allow_http IN(0,1)),
 allow_private INTEGER NOT NULL DEFAULT 0 CHECK(allow_private IN(0,1)),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(protocol,host,port,path_prefix)
);
CREATE TABLE ai_endpoint_capabilities(
 endpoint_id TEXT NOT NULL REFERENCES ai_trusted_endpoints(id),
 scenario TEXT NOT NULL CHECK(scenario IN('ocr','journal','chat')),
 model TEXT NOT NULL CHECK(length(trim(model)) BETWEEN 1 AND 200),
 response_format TEXT NOT NULL CHECK(response_format IN('json_schema','json_object')),
 supports_image INTEGER NOT NULL DEFAULT 0 CHECK(supports_image IN(0,1)),
 context_limit INTEGER NOT NULL CHECK(context_limit BETWEEN 1 AND 1000000),
 output_limit INTEGER NOT NULL CHECK(output_limit BETWEEN 1 AND 1000000),
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 90),
 auth_mode TEXT NOT NULL CHECK(auth_mode IN('bearer','none')),
 pricing_version TEXT NOT NULL DEFAULT '',
 data_location TEXT NOT NULL CHECK(length(trim(data_location)) BETWEEN 1 AND 120),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 created_at TEXT NOT NULL,
 PRIMARY KEY(endpoint_id,scenario)
);
CREATE TABLE ai_company_policies(
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 managed INTEGER NOT NULL DEFAULT 1 CHECK(managed IN(0,1)),
 local_only INTEGER NOT NULL DEFAULT 0 CHECK(local_only IN(0,1)),
 allowed_scenarios_json TEXT NOT NULL CHECK(json_valid(allowed_scenarios_json) AND json_type(allowed_scenarios_json)='array'),
 allowed_data_categories_json TEXT NOT NULL CHECK(json_valid(allowed_data_categories_json) AND json_type(allowed_data_categories_json)='array'),
 allowed_endpoint_ids_json TEXT NOT NULL CHECK(json_valid(allowed_endpoint_ids_json) AND json_type(allowed_endpoint_ids_json)='array'),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 updated_by TEXT NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id,version),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE TRIGGER ai_company_policies_no_update BEFORE UPDATE ON ai_company_policies
BEGIN SELECT RAISE(ABORT,'AI policy history is immutable'); END;
CREATE TRIGGER ai_company_policies_no_delete BEFORE DELETE ON ai_company_policies
BEGIN SELECT RAISE(ABORT,'AI policy history is immutable'); END;
CREATE TRIGGER ai_endpoint_capabilities_no_update BEFORE UPDATE ON ai_endpoint_capabilities
BEGIN SELECT RAISE(ABORT,'AI endpoint capability history is immutable'); END;
CREATE TRIGGER ai_endpoint_capabilities_no_delete BEFORE DELETE ON ai_endpoint_capabilities
BEGIN SELECT RAISE(ABORT,'AI endpoint capability history is immutable'); END;
CREATE TRIGGER ai_trusted_endpoints_no_delete BEFORE DELETE ON ai_trusted_endpoints
BEGIN SELECT RAISE(ABORT,'AI trusted endpoints are retained for audit'); END;
