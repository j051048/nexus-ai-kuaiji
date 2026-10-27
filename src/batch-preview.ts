/** Read model for the monthly-open precheck.
 *
 * The dialog must never imply an outcome the server did not compute: unknown
 * codes stay verbatim, and a response without per-company rows is rejected so the
 * operator falls back to "还没有预检" instead of seeing invented numbers. */
export type BatchPreviewRow = {
  company_id: string;
  company_name: string;
  status: 'ready' | 'skipped' | 'blocked';
  code?: string;
  detail?: string;
  from_period?: string;
};
export type BatchPreview = {
  period: string;
  ready: number;
  skipped: number;
  blocked: number;
  results: BatchPreviewRow[];
  notes: string[];
};
export type PreviewLine = {
  key: string;
  name: string;
  tone: 'ready' | 'skipped' | 'blocked';
  statusText: string;
};

const codeText: Record<string, string> = {
  already_open: '该月份账套已存在',
  no_history: '没有历史账期，需先首次接入',
  no_access: '不是本所可管理的客户',
  not_client: '不是本所的在册客户',
};

export function validateBatchPreview(value: unknown): value is BatchPreview {
  if (!value || typeof value !== 'object') return false;
  const preview = value as Partial<BatchPreview>;
  if (typeof preview.period !== 'string' || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(preview.period))
    return false;
  if (!Array.isArray(preview.results) || !Array.isArray(preview.notes)) return false;
  return preview.results.every((row) => {
    if (!row || typeof row !== 'object') return false;
    const item = row as Partial<BatchPreviewRow>;
    return (
      typeof item.company_id === 'string' &&
      typeof item.company_name === 'string' &&
      ['ready', 'skipped', 'blocked'].includes(item.status ?? '')
    );
  });
}

export function previewLines(preview: BatchPreview | null, limit = 6): PreviewLine[] {
  if (!preview) return [];
  return preview.results
    .filter((row) => row.status !== 'ready')
    .slice(0, Math.max(1, limit))
    .map((row) => ({
      key: row.company_id,
      name: row.company_name || row.company_id,
      tone: row.status === 'blocked' ? 'blocked' : 'skipped',
      statusText:
        row.detail ||
        (row.code && codeText[row.code]) ||
        (row.status === 'skipped' ? '已存在，将跳过' : '需先处理'),
    }));
}

export function previewVerdict(preview: BatchPreview | null) {
  if (!preview) return { canRun: false, text: '尚未预检：先预检范围，再执行开期。' };
  if (!preview.ready)
    return {
      canRun: false,
      text: `没有可直接开期的企业（跳过 ${preview.skipped} 家、阻塞 ${preview.blocked} 家）。`,
    };
  return {
    canRun: true,
    text: `将开期 ${preview.ready} 家；跳过 ${preview.skipped} 家、阻塞 ${preview.blocked} 家，逐户结果在执行后仍会保留。`,
  };
}
