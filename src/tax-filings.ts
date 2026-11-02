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
    });
  }
  return rows;
}

export function formatYuan(cents: number): string {
  return (cents / 100).toFixed(2);
}
