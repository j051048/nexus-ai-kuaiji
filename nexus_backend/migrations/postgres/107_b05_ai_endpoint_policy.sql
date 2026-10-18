-- compatibility: incremental
-- B05 trusted endpoints and versioned company AI egress policy.

CREATE TABLE IF NOT EXISTS accounting.ai_trusted_endpoints(
 id TEXT PRIMARY KEY, protocol TEXT NOT NULL CHECK(protocol IN('https','http')),
 host TEXT NOT NULL, port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
 path_prefix TEXT NOT NULL DEFAULT '',
 service_identity TEXT NOT NULL CHECK(length(btrim(service_identity)) BETWEEN 1 AND 120),
 allow_http INTEGER NOT NULL DEFAULT 0 CHECK(allow_http IN(0,1)),
 allow_private INTEGER NOT NULL DEFAULT 0 CHECK(allow_private IN(0,1)),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,
 UNIQUE(protocol,host,port,path_prefix)
);
CREATE TABLE IF NOT EXISTS accounting.ai_endpoint_capabilities(
 endpoint_id TEXT NOT NULL REFERENCES accounting.ai_trusted_endpoints(id),
 scenario TEXT NOT NULL CHECK(scenario IN('ocr','journal','chat')),
 model TEXT NOT NULL, response_format TEXT NOT NULL CHECK(response_format IN('json_schema','json_object')),
 supports_image INTEGER NOT NULL DEFAULT 0 CHECK(supports_image IN(0,1)),
 context_limit INTEGER NOT NULL CHECK(context_limit BETWEEN 1 AND 1000000),
 output_limit INTEGER NOT NULL CHECK(output_limit BETWEEN 1 AND 1000000),
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 90),
 auth_mode TEXT NOT NULL CHECK(auth_mode IN('bearer','none')),
 pricing_version TEXT NOT NULL DEFAULT '',
 data_location TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN(0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), reason TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(endpoint_id,scenario)
);
CREATE TABLE IF NOT EXISTS accounting.ai_company_policies(
 firm_id TEXT NOT NULL, company_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 managed INTEGER NOT NULL DEFAULT 1 CHECK(managed IN(0,1)),
 local_only INTEGER NOT NULL DEFAULT 0 CHECK(local_only IN(0,1)),
 allowed_scenarios_json TEXT NOT NULL, allowed_data_categories_json TEXT NOT NULL,
 allowed_endpoint_ids_json TEXT NOT NULL, reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 updated_by TEXT NOT NULL REFERENCES accounting.users(id), updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id,version),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
ALTER TABLE accounting.ai_company_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ai_company_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY b05_policy_read ON accounting.ai_company_policies FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm());
CREATE POLICY b05_policy_insert ON accounting.ai_company_policies FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm());
GRANT SELECT,INSERT ON accounting.ai_trusted_endpoints,accounting.ai_endpoint_capabilities,
 accounting.ai_company_policies TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.b05_policy_append_only() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP IN('UPDATE','DELETE') THEN
  RAISE EXCEPTION 'B05 policy history is append-only' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.b05_policy_append_only() FROM PUBLIC;
CREATE TRIGGER b05_policy_append BEFORE UPDATE OR DELETE ON accounting.ai_company_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.b05_policy_append_only();
CREATE TRIGGER b05_caps_append BEFORE UPDATE OR DELETE ON accounting.ai_endpoint_capabilities
 FOR EACH ROW EXECUTE FUNCTION accounting.b05_policy_append_only();
