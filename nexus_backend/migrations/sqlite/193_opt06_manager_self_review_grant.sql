-- OPT06-B: managers cannot grant reviewer to themselves or another manager.
CREATE TRIGGER book_access_separation_guard
BEFORE INSERT ON book_access
WHEN NEW.role='reviewer'
 AND EXISTS(SELECT 1 FROM firm_review_controls c WHERE c.firm_id=NEW.firm_id AND c.management_review_separation=1)
 AND EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=NEW.firm_id AND m.user_id=NEW.user_id AND m.role IN('owner','admin'))
BEGIN
 SELECT RAISE(ABORT,'Management/review separation forbids manager reviewer grants');
END;
