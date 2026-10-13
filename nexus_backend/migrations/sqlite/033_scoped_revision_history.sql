-- SQLite local-mode history is projected from pinned pre-replacement snapshots.
-- Authentication and current book authorization remain enforced by the service.
CREATE VIEW revision_history_snapshots AS SELECT l.revision_id,l.firm_id,l.event_id,l.old_book_id,l.old_source_id,l.old_draft_id,l.old_fact_version,
 r.old_snapshot,r.reason,r.created_at,
 json_object('event_version',v.event_version,'payload',json(v.payload),'template_snapshot',json(v.template_snapshot),
  'attachment_snapshot',json(v.attachment_snapshot),'created_at',v.created_at,'created_by',v.created_by) AS submitted_snapshot
 FROM fact_revision_lineage l JOIN fact_revisions r ON r.id=l.revision_id AND r.firm_id=l.firm_id AND r.state='replaced'
 JOIN business_versions v ON v.event_id=l.event_id AND v.event_version=json_extract(r.old_snapshot,'$.mapping.submitted_version');
