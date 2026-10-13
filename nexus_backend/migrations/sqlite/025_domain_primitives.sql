-- Observable contract version; existing stored amounts remain integer cents.
CREATE TABLE domain_contracts (
 version INTEGER PRIMARY KEY CHECK(version=1),
 max_cents INTEGER NOT NULL CHECK(max_cents=99999999999999),
 minimum_year INTEGER NOT NULL CHECK(minimum_year=2000),
 maximum_year INTEGER NOT NULL CHECK(maximum_year=2099)
);
INSERT INTO domain_contracts VALUES(1,99999999999999,2000,2099);
CREATE TRIGGER domain_contracts_immutable_update BEFORE UPDATE ON domain_contracts
BEGIN SELECT RAISE(ABORT,'Domain contracts are migration-owned'); END;
CREATE TRIGGER domain_contracts_immutable_delete BEFORE DELETE ON domain_contracts
BEGIN SELECT RAISE(ABORT,'Domain contracts are migration-owned'); END;
