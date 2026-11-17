-- OPT06-B: rr_book_permissions must reflect firm separation policy.
CREATE OR REPLACE VIEW accounting.rr_book_permissions AS SELECT b.id AS book_id,u.id AS user_id,
 (m.role IN('owner','admin') AND NOT coalesce(frc.management_review_separation,false)
   OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))) AS can_edit,
 (m.role IN('owner','admin') AND NOT coalesce(frc.management_review_separation,false)
   OR ba.role='reviewer' AND m.role='reviewer') AS can_review
 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.active=1 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.access_all_book_roles ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 LEFT JOIN accounting.firm_review_controls frc ON frc.firm_id=b.firm_id;
