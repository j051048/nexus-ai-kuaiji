export interface CloseScope {
  bookId: string;
  period: string;
}
type ObjectValue = Record<string, unknown>;
function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('月结响应格式不完整，请重新读取。');
  return value as ObjectValue;
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('月结身份字段缺失，请重新读取。');
  return value;
}
function integer(value: unknown, positive = false): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || (positive && value < 1))
    throw new Error('月结版本或金额格式不正确。');
  return value;
}
function list(value: unknown, key: string): unknown[] {
  const rows = object(value)[key];
  if (!Array.isArray(rows)) throw new Error('月结清单格式不完整。');
  return rows;
}
function bound(value: unknown, scope: CloseScope, legacy = false) {
  const row = object(value);
  if (row.book_id !== scope.bookId || row.period !== scope.period)
    throw new Error('响应与当前账套月份不一致，未接纳。');
  return {
    row,
    id: text(row.id),
    version: integer(row.version, true),
    created_by: legacy && row.created_by === null ? '' : text(row.created_by),
  };
}
export interface ReportRow {
  id: string;
  version: number;
  status: string;
  created_by: string;
  content_hash: string;
  content: ReportContent;
}
export interface ReportContent {
  balance_sheet: Record<'assets' | 'liabilities' | 'equity' | 'unclosed_profit', number> & {
    items: ReportItem[];
  };
  profit_statement: Record<'revenue_cents' | 'expense_cents' | 'profit_cents', number> & {
    items: ReportItem[];
  };
  cash_flow: Record<string, number>;
  checks: Record<string, boolean>;
}
interface ReportItem {
  account_id: string;
  code: string;
  name: string;
  amount_cents: number;
}
function parseReportContent(raw: unknown): ReportContent {
  const content = object(JSON.parse(text(raw)));
  const balance = object(content.balance_sheet),
    profit = object(content.profit_statement),
    cash = object(content.cash_flow),
    checks = object(content.checks);
  function items(value: unknown): ReportItem[] {
    if (!Array.isArray(value)) throw new Error('报表科目明细不完整，禁止盲目复核。');
    return value.map((rawItem) => {
      const item = object(rawItem);
      return {
        account_id: text(item.account_id),
        code: text(item.code),
        name: text(item.name),
        amount_cents: integer(item.amount_cents),
      };
    });
  }
  const moneyFields = (value: ObjectValue, fields: string[]) =>
    Object.fromEntries(fields.map((field) => [field, integer(value[field])]));
  const validatedChecks: Record<string, boolean> = {};
  for (const key of ['balance_sheet_passed', 'profit_statement_passed', 'cash_flow_passed']) {
    if (typeof checks[key] !== 'boolean') throw new Error('报表勾稽状态缺失。');
    validatedChecks[key] = checks[key];
  }
  return {
    balance_sheet: {
      assets: integer(balance.assets),
      liabilities: integer(balance.liabilities),
      equity: integer(balance.equity),
      unclosed_profit: integer(balance.unclosed_profit),
      items: items(balance.items),
    },
    profit_statement: {
      revenue_cents: integer(profit.revenue_cents),
      expense_cents: integer(profit.expense_cents),
      profit_cents: integer(profit.profit_cents),
      items: items(profit.items),
    },
    cash_flow: moneyFields(cash, [
      'opening_cash_cents',
      'closing_cash_cents',
      'net_change_cents',
      'operating_cents',
      'investing_cents',
      'financing_cents',
      'unclassified_cents',
    ]),
    checks: validatedChecks,
  };
}
export function parseReportRows(value: unknown, scope: CloseScope): ReportRow[] {
  return list(value, 'reports').map((raw) => {
    const { row, ...identity } = bound(raw, scope);
    if (!['draft', 'validated', 'reviewed', 'published'].includes(String(row.status)))
      throw new Error('报表状态未知。');
    const hash = text(row.content_hash);
    if (!/^[a-f0-9]{64}$/.test(hash)) throw new Error('报表指纹无效。');
    return {
      ...identity,
      status: String(row.status),
      content_hash: hash,
      content: parseReportContent(row.content_json),
    };
  });
}
/** A destination is optional evidence: older runs predate it, and a malformed
 * one is dropped so the panel explains the gap instead of routing blindly. */
function parseActionTarget(
  value: unknown,
): { kind: string; book_id: string; period: string } | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const row = value as Record<string, unknown>;
  if (typeof row.kind !== 'string' || !row.kind || row.kind.length > 40) return undefined;
  if (typeof row.book_id !== 'string' || !row.book_id || row.book_id.length > 100)
    return undefined;
  if (typeof row.period !== 'string' || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(row.period))
    return undefined;
  return { kind: row.kind, book_id: row.book_id, period: row.period };
}
export interface CloseCheck {
  code: string;
  status: 'pass' | 'blocked';
  detail: ObjectValue;
  owner_role?: string;
  next_action?: string;
  action_target?: { kind: string; book_id: string; period: string };
}
export interface CloseRow {
  id: string;
  version: number;
  status: string;
  created_by: string;
  report_run_id: string;
  checks: CloseCheck[];
}
export function parseCloseRows(value: unknown, scope: CloseScope): CloseRow[] {
  return list(value, 'runs').map((raw) => {
    const { row, ...identity } = bound(raw, scope);
    if (
      !['preflight_failed', 'preflight_passed', 'reviewed', 'locked', 'published'].includes(
        String(row.status),
      )
    )
      throw new Error('月结状态未知。');
    const checks: unknown = JSON.parse(text(row.checks_json));
    if (!Array.isArray(checks)) throw new Error('月结检查项不完整。');
    return {
      ...identity,
      status: String(row.status),
      report_run_id: text(row.report_run_id),
      checks: checks.map((rawCheck) => {
        const check = object(rawCheck);
        if (check.status !== 'pass' && check.status !== 'blocked')
          throw new Error('月结检查状态未知。');
        // Some count checks legitimately use numbers; evidence checks must retain the entire diagnosis.
        const detail =
          typeof check.detail === 'number'
            ? { count: integer(check.detail) }
            : typeof check.detail === 'string'
              ? { reason: check.detail }
              : check.code === 'subledger_control' && Array.isArray(check.detail)
                ? {
                    rows: check.detail,
                    reason: '历史预检仅保留科目明细；请重新读取当前诊断并生成新预检。',
                  }
                : object(check.detail);
        return {
          code: text(check.code),
          status: check.status,
          detail,
          owner_role: typeof check.owner_role === 'string' ? check.owner_role : undefined,
          next_action: typeof check.next_action === 'string' ? check.next_action : undefined,
          action_target: parseActionTarget(check.action_target),
        };
      }),
    };
  });
}
export interface CoverageRow {
  id: string;
  version: number;
  created_by: string;
  account_id: string;
  state: string;
  opening_cents: number;
  closing_cents: number;
  source_sha256: string;
  source_file_id: string;
  source_preview_id: string;
}
export function parseCoverageRows(value: unknown, scope: CloseScope): CoverageRow[] {
  return list(value, 'coverages').map((raw) => {
    const legacy = object(raw).state === 'legacy_unverified';
    const { row, ...identity } = bound(raw, scope, legacy);
    if (
      !['pending_review', 'verified', 'rejected', 'legacy_unverified'].includes(String(row.state))
    )
      throw new Error('银行覆盖状态未知。');
    const hash = legacy && row.source_sha256 === null ? '' : text(row.source_sha256);
    if (hash !== '' && !/^[a-f0-9]{64}$/.test(hash)) throw new Error('银行覆盖证据指纹无效。');
    return {
      ...identity,
      account_id: text(row.account_id),
      state: String(row.state),
      opening_cents: integer(row.opening_cents),
      closing_cents: integer(row.closing_cents),
      source_sha256: hash,
      source_file_id: legacy && row.source_file_id === null ? '' : text(row.source_file_id),
      source_preview_id:
        legacy && row.source_preview_id === null ? '' : text(row.source_preview_id),
    };
  });
}
export function parseReconciliation(value: unknown): ObjectValue & { passed: boolean } {
  const result = object(value);
  if (typeof result.passed !== 'boolean') throw new Error('对账结果不完整，请重新读取。');
  if (result.rows !== undefined) {
    if (!Array.isArray(result.rows)) throw new Error('对账明细不完整。');
    for (const raw of result.rows) {
      const row = object(raw);
      if (typeof row.passed !== 'boolean') throw new Error('对账明细状态不完整。');
      for (const field of [
        'difference_cents',
        'book_difference_cents',
        'opening_book_difference_cents',
        'book_cents',
        'subledger_cents',
        'opening_cents',
        'closing_cents',
        'net_cents',
        'unmatched_count',
      ]) {
        if (row[field] !== undefined) integer(row[field]);
      }
      if (row.coverage_state !== undefined && typeof row.coverage_state !== 'string')
        throw new Error('银行原件覆盖状态不完整。');
    }
  }
  return { ...result, passed: result.passed };
}
export function canIndependentlyReview(
  item: { created_by: string },
  userId: string,
  canReview: boolean,
): boolean {
  return canReview && !!item.created_by && !!userId && item.created_by !== userId;
}
