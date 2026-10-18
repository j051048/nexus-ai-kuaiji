-- New consumptions must reference an actual original in the same transaction.
-- Historical consumed rows keep NULL binding_json: never fabricate a backfill.
ALTER TABLE accounting.upload_intents ADD COLUMN binding_json TEXT;
CREATE FUNCTION accounting.upload_binding_expected(purpose TEXT, object_key TEXT, uploader TEXT, firm TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 CASE purpose
 WHEN 'intake_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','intake_attachment','target_kind','intake_draft','target_id',a.intake_id,'attachment_table','intake_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.intake_attachments a JOIN accounting.intake_drafts t ON t.id=a.intake_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'event_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','event_attachment','target_kind','business_event','target_id',a.event_id,'attachment_table','business_attachments','attachment_id',a.id,'event_id',t.id,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.business_attachments a JOIN accounting.business_events t ON t.id=a.event_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'case_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','case_attachment','target_kind','case_task','target_id',a.case_id,'attachment_table','case_attachments','attachment_id',a.id,'event_id',t.event_id,'source_id',t.source_id,'book_id',t.book_id,'response_id',a.response_id,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.case_attachments a JOIN accounting.case_tasks t ON t.id=a.case_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'material_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','material_attachment','target_kind','material_request','target_id',a.request_id,'attachment_table','material_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.book_id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.material_attachments a JOIN accounting.material_requests t ON t.id=a.request_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'bank_import' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','bank_import','target_kind','book','target_id',a.book_id,'attachment_table','bank_import_files','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.bank_import_files a JOIN accounting.books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'invoice_import' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','invoice_import','target_kind','book','target_id',a.book_id,'attachment_table','invoice_import_batches','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.invoice_import_batches a JOIN accounting.books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 ELSE RETURN NULL;
 END CASE;
END $$;
REVOKE ALL ON FUNCTION accounting.upload_binding_expected(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE FUNCTION accounting.upload_binding_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE expected JSONB; supplied JSONB;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'pending' OR NEW.consumed_ref IS NOT NULL OR NEW.binding_json IS NOT NULL THEN RAISE EXCEPTION 'Upload binding cannot be supplied on creation' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF OLD.state='consumed' THEN
  IF NEW.state IS DISTINCT FROM OLD.state OR NEW.binding_json IS DISTINCT FROM OLD.binding_json OR NEW.consumed_ref IS DISTINCT FROM OLD.consumed_ref THEN
   RAISE EXCEPTION 'Consumed upload binding is immutable, including legacy rows' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.state<>'consumed' THEN
  IF NEW.binding_json IS NOT NULL OR NEW.consumed_ref IS NOT NULL THEN RAISE EXCEPTION 'Unconsumed upload cannot have a binding' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF OLD.state<>'consuming' THEN RAISE EXCEPTION 'Consume original atomically' USING ERRCODE='23514'; END IF;
 expected:=accounting.upload_binding_expected(NEW.purpose,NEW.storage_key,NEW.user_id,NEW.firm_id);
 BEGIN supplied:=NEW.binding_json::jsonb; EXCEPTION WHEN OTHERS THEN RAISE EXCEPTION 'Invalid upload binding JSON' USING ERRCODE='23514'; END;
 IF expected IS NULL OR supplied IS DISTINCT FROM expected
  OR NEW.consumed_ref IS DISTINCT FROM (expected->>'attachment_table')||':'||(expected->>'attachment_id')
  OR expected->'storage' IS DISTINCT FROM jsonb_build_object('storage_key',NEW.storage_key,'sha256',NEW.sha256,'size_bytes',NEW.size_bytes)
 THEN RAISE EXCEPTION 'Upload binding must match the actual company, parent and original bytes' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_upload_binding_guard BEFORE INSERT OR UPDATE ON accounting.upload_intents FOR EACH ROW EXECUTE FUNCTION accounting.upload_binding_guard();
REVOKE ALL ON FUNCTION accounting.upload_binding_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
