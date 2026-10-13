-- Reviewed clearance evidence remains readable, but cannot become a generic
-- bank classifier without a settlement-aware rule language and evaluation.
CREATE FUNCTION accounting.learning_sample_ordinary(target_book TEXT,target_sample TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(SELECT 1 FROM accounting.learning_samples x WHERE x.id=target_sample AND x.book_id=target_book AND x.firm_id=accounting.actor_firm()
 AND NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.settlement_allocations a ON a.bank_source_id=s.id WHERE s.id=x.source_id AND s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM accounting.settlement_reversals r WHERE r.allocation_id=a.id)))
$$;
CREATE FUNCTION accounting.learning_ordinary_examples_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE refs JSONB; positives JSONB; negatives JSONB; BEGIN
 IF TG_TABLE_NAME='learning_rule_versions' THEN refs:=NEW.sample_ids_json::jsonb;
 ELSIF TG_TABLE_NAME='learning_rule_evaluations' THEN
  SELECT v.sample_ids_json::jsonb INTO positives FROM accounting.learning_rule_versions v WHERE v.rule_id=NEW.rule_id AND v.book_id=NEW.book_id AND v.version=NEW.rule_version;
  refs:=COALESCE(positives,'[]'::jsonb)||NEW.negative_sample_ids_json::jsonb;
 ELSE
  IF NEW.status<>'published' THEN RETURN NEW;END IF;
  SELECT v.sample_ids_json::jsonb INTO positives FROM accounting.learning_rule_versions v WHERE v.rule_id=NEW.id AND v.book_id=NEW.book_id AND v.version=NEW.current_version;
  SELECT e.negative_sample_ids_json::jsonb INTO negatives FROM accounting.learning_rule_evaluations e WHERE e.id=NEW.evaluation_id AND e.rule_id=NEW.id AND e.book_id=NEW.book_id AND e.rule_version=NEW.current_version;
  refs:=COALESCE(positives,'[]'::jsonb)||COALESCE(negatives,'[]'::jsonb);
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(refs) AS selected(sample_id) WHERE NOT accounting.learning_sample_ordinary(NEW.book_id,selected.sample_id)) THEN
  RAISE EXCEPTION 'Clearance samples are trace evidence, not ordinary classification examples' USING ERRCODE='23514';END IF;
 RETURN NEW;END $$;
CREATE TRIGGER learning_ordinary_version BEFORE INSERT ON accounting.learning_rule_versions FOR EACH ROW EXECUTE FUNCTION accounting.learning_ordinary_examples_guard();
CREATE TRIGGER learning_ordinary_evaluation BEFORE INSERT ON accounting.learning_rule_evaluations FOR EACH ROW EXECUTE FUNCTION accounting.learning_ordinary_examples_guard();
CREATE TRIGGER learning_ordinary_publish BEFORE UPDATE OF status ON accounting.learning_rules FOR EACH ROW EXECUTE FUNCTION accounting.learning_ordinary_examples_guard();
REVOKE ALL ON FUNCTION accounting.learning_sample_ordinary(TEXT,TEXT),accounting.learning_ordinary_examples_guard() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.learning_sample_ordinary(TEXT,TEXT) TO accounting_app;
