-- Public-input primitives only; no tenant data or elevated data access.
CREATE FUNCTION accounting.domain_money_cents(value TEXT) RETURNS BIGINT
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
BEGIN
 IF value !~ '^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$' OR value ~ E'[\n\r]'
 THEN RAISE EXCEPTION 'Invalid decimal money' USING ERRCODE='22023'; END IF;
 RETURN (value::NUMERIC*100)::BIGINT;
END $$;
CREATE FUNCTION accounting.domain_period_valid(value TEXT) RETURNS BOOLEAN
LANGUAGE SQL IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(value ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' AND length(value)=7,false)
$$;
REVOKE ALL ON FUNCTION accounting.domain_money_cents(TEXT),accounting.domain_period_valid(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.domain_money_cents(TEXT),accounting.domain_period_valid(TEXT) TO accounting_app,accounting_worker;

-- Existing rule/receipt guards keep their nullable contract but delegate the
-- actual decimal grammar and range to the shared domain primitive.
CREATE OR REPLACE FUNCTION accounting.learning_money_cents(value TEXT) RETURNS BIGINT
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 RETURN accounting.domain_money_cents(value);
EXCEPTION WHEN invalid_parameter_value OR numeric_value_out_of_range THEN
 RETURN NULL;
END $$;
