-- An uncertainty observation may later receive this request's actual token
-- report; the source identity, tariff and reservation always remain unchanged.
DROP TRIGGER model_cost_identity;
CREATE TRIGGER model_cost_identity BEFORE UPDATE ON model_costs
 WHEN NEW.id IS NOT OLD.id OR NEW.run_id IS NOT OLD.run_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.kind IS NOT OLD.kind OR NEW.created_by IS NOT OLD.created_by OR NEW.period IS NOT OLD.period
 OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model OR NEW.charge_token IS NOT OLD.charge_token OR NEW.pricing_json IS NOT OLD.pricing_json
 OR NEW.quota_enabled IS NOT OLD.quota_enabled OR NEW.reserved_micro IS NOT OLD.reserved_micro OR NEW.created_at IS NOT OLD.created_at
 OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS NOT OLD.sent_at)
 OR (OLD.state IN ('estimated','released','reconciled') AND NEW.state<>'reconciled')
 OR (OLD.state='uncertain' AND NOT (NEW.state='reconciled' OR (OLD.estimated_micro IS NULL AND OLD.input_tokens IS NULL AND OLD.output_tokens IS NULL AND NEW.state IN ('estimated','released','uncertain'))))
 OR (OLD.state IN ('estimated','released','reconciled') AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 OR (OLD.state='uncertain' AND (OLD.estimated_micro IS NOT NULL OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL) AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 BEGIN SELECT RAISE(ABORT,'Model cost evidence and provenance are immutable'); END;
