export interface TaxFiling {
  id: string;
  period: string;
  kind: string;
  kind_label: string;
  status: 'filed' | 'paid';
  status_label: string;
  external_ref: string;
  occurred_at: string;
  amount_cents: number;
  evidence_note: string;
  entry_kind: 'registration' | 'correction' | 'void';
  supersedes_id: string | null;
  correction_reason: string;
  workpaper_version: number | null;
  workpaper_status: string | null;
  amount_check: string;
  current: boolean;
  attachment: {
    id: string;
    filename: string;
    sha256: string;
    size_bytes: number;
  } | null;
}

export function parseFilings(raw: unknown): TaxFiling[] {
  const list = Array.isArray((raw as { filings?: unknown })?.filings)
    ? (raw as { filings: unknown[] }).filings
    : [];
  const rows: TaxFiling[] = [];
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (row.status !== 'filed' && row.status !== 'paid') continue;
    if (typeof row.external_ref !== 'string' || !row.external_ref) continue;
    const attachment = row.attachment as Record<string, unknown> | null | undefined;
    rows.push({
      id: typeof row.id === 'string' ? row.id : '',
      period: typeof row.period === 'string' ? row.period : '',
      kind: typeof row.kind === 'string' ? row.kind : '',
      kind_label: typeof row.kind_label === 'string' ? row.kind_label : '税种',
      status: row.status,
      status_label: typeof row.status_label === 'string' ? row.status_label : row.status,
      external_ref: row.external_ref,
      occurred_at: typeof row.occurred_at === 'string' ? row.occurred_at : '',
      amount_cents: typeof row.amount_cents === 'number' ? row.amount_cents : 0,
      evidence_note: typeof row.evidence_note === 'string' ? row.evidence_note : '',
      entry_kind:
        row.entry_kind === 'correction' || row.entry_kind === 'void'
          ? row.entry_kind
          : 'registration',
      supersedes_id: typeof row.supersedes_id === 'string' ? row.supersedes_id : null,
      correction_reason: typeof row.correction_reason === 'string' ? row.correction_reason : '',
      workpaper_version: typeof row.workpaper_version === 'number' ? row.workpaper_version : null,
      workpaper_status: typeof row.workpaper_status === 'string' ? row.workpaper_status : null,
      amount_check: typeof row.amount_check === 'string' ? row.amount_check : 'no_workpaper',
      current: row.current !== false,
      attachment:
        attachment && typeof attachment.id === 'string'
          ? {
              id: attachment.id,
              filename: typeof attachment.filename === 'string' ? attachment.filename : '回执原件',
              sha256: typeof attachment.sha256 === 'string' ? attachment.sha256 : '',
              size_bytes: typeof attachment.size_bytes === 'number' ? attachment.size_bytes : 0,
            }
          : null,
    });
  }
  return rows;
}

export function formatYuan(cents: number): string {
  return (cents / 100).toFixed(2);
}
