import type { MigrationAccountMapping, MigrationCategory, MigrationColumnMapping, MigrationDetail, MigrationFile, MigrationJob, MigrationProject, MigrationRecord, MigrationRecordKind } from './legacy-migration-types';

export const MIGRATION_PRODUCTS = { chanjet_tplus: '畅捷通 T+', yonyou: '用友', kingdee: '金蝶', generic: '通用资料包' };
export const MIGRATION_CATEGORIES: Record<MigrationCategory, string> = { package: '完整资料包（JSON）', accounts: '会计科目', auxiliaries: '辅助档案', vouchers: '历史凭证分录', balances: '科目与辅助余额', open_items: '未结往来', attachment: '凭证附件原件' };
export const MIGRATION_STEPS = ['上传旧账', '后台解析', '科目与辅助映射', '核对差异', '确认接管'];
export const MIGRATION_RECORD_KINDS: Record<MigrationRecordKind, string> = { accounts: 'account', auxiliaries: 'auxiliary', vouchers: 'voucher', balances: 'balance', open_items: 'open_item' };
export const MIGRATION_STATE_LABELS: Record<string, string> = { draft: '待上传', collecting: '资料收集中', uploaded: '待解析', parsing: '解析中', staged: '待映射与核对', parsed: '待映射与核对', mapped: '待校验', validating: '校验中', validated: '待独立验收', ready: '待独立验收', needs_attention: '差异待处理', accepted: '待确认接管', activated: '已接管', active: '已接管', failed: '需处理', cancelled: '已取消', stale: '资料已变化，请重验', queued: '排队中', running: '执行中', succeeded: '已完成', completed: '已完成', interrupted: '执行中断', retry_wait: '等待重试' };
export function migrationState(state: string): string { return MIGRATION_STATE_LABELS[state] ?? state; }
export function migrationActive(job: MigrationJob): boolean { return ['queued', 'running', 'retry_wait'].includes(job.state); }
export function migrationFrozen(project: MigrationProject): boolean { return ['activated', 'active'].includes(project.state); }
export function migrationNeedsColumnConfirmation(file: MigrationFile): boolean { return !file.superseded && !['package', 'attachment'].includes(file.category) && /\.(csv|xlsx)$/i.test(file.filename) && file.mapping?.confirmed !== true; }
export function migrationArchiveOnlyAccount(record: MigrationRecord): boolean { return record.kind === 'account' && (record.data ?? record.payload ?? record).posting === false; }
export function migrationPostingMappings(edits: MigrationAccountMapping[], records: MigrationRecord[]): MigrationAccountMapping[] {
  const parents = new Set(records.filter(migrationArchiveOnlyAccount).map(record => { const payload = record.data ?? record.payload ?? record; return String(record.source_code ?? record.code ?? payload.source_code ?? payload.code ?? record.external_id ?? ''); }));
  return edits.filter(edit => !parents.has(edit.source_code));
}
export function migrationAcceptanceCurrent(detail: MigrationDetail): boolean {
  return !!detail.acceptance && !!detail.latest_validation && detail.latest_validation.current !== false && detail.acceptance.validation_id === detail.latest_validation.id && detail.acceptance.basis_token === detail.latest_validation.basis_token;
}
export function migrationStep(detail: MigrationDetail): number {
  if (migrationFrozen(detail.project) || migrationAcceptanceCurrent(detail)) return 4;
  if (detail.latest_validation || detail.jobs.some(job => job.kind === 'validate' && migrationActive(job))) return 3;
  if ((detail.mapping?.version ?? 0) > 0 || ['parsed', 'mapped'].includes(detail.project.state) || detail.jobs.some(job => job.kind === 'parse' && job.state === 'succeeded')) return 2;
  if (detail.files.length || detail.jobs.length) return 1;
  return 0;
}
/** Money remains exact even above Number.MAX_SAFE_INTEGER; no floating point summaries. */
export function migrationMoney(value: unknown, minorUnits = false): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value !== 'string' && (!minorUnits || typeof value !== 'number' || !Number.isSafeInteger(value))) return '金额格式待核对';
  const raw = String(value);
  if (minorUnits) {
    if (!/^-?\d+$/.test(raw)) return '金额格式待核对';
    const n = BigInt(raw), abs = n < 0n ? -n : n;
    return `${n < 0n ? '-' : ''}${(abs / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${(abs % 100n).toString().padStart(2, '0')}`;
  }
  if (!/^-?\d+(?:\.\d{1,2})?$/.test(raw)) return '金额格式待核对';
  const [whole, fraction = ''] = raw.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}
export function migrationUnsignedCents(value: string): bigint | null {
  if (value.length > 30 || !/^\d+(?:\.\d{1,2})?$/.test(value)) return null;
  const [whole, part = ''] = value.split('.');
  return BigInt(whole) * 100n + BigInt(part.padEnd(2, '0'));
}
export const MIGRATION_FIELD_LABELS: Record<string, string> = {
  external_id: '原始记录 ID', code: '原科目 / 档案编码', name: '原科目 / 档案名称', parent_code: '上级科目编码', posting: '是否末级科目',
  category: '科目类别', normal_balance: '正常余额方向', auxiliary_requirements: '辅助核算要求', kind: '档案 / 往来类型',
  date: '凭证日期', period: '会计期间', voucher_type: '凭证字', number: '凭证号', status: '凭证状态', original_status: '原软件状态', is_reversal: '红字 / 冲销',
  account_code: '原科目编码', description: '业务摘要', currency: '币种', original_currency: '原币币种', auxiliaries: '辅助核算',
  opening: '期初净余额（借正贷负）', period_debit: '本期借方发生额', period_credit: '本期贷方发生额', closing: '期末净余额（借正贷负）',
  opening_debit: '期初借方余额', opening_credit: '期初贷方余额', closing_debit: '期末借方余额', closing_credit: '期末贷方余额',
  year_debit: '本年借方累计', year_credit: '本年贷方累计', scope: '余额口径', counterparty_code: '原往来方编码',
  original_amount: '原始往来金额', settled_amount: '原软件已核销金额', remaining_amount: '接管未结金额', occurred_on: '原业务日期', due_on: '到期日期',
};
const MIGRATION_VALUE_LABELS: Record<string, string> = { posted: '已记账', unposted: '未记账', void: '作废', asset: '资产', liability: '负债', equity: '权益', cost: '成本', income: '收入', expense: '费用', other: '其他', debit: '借方', credit: '贷方', receivable: '应收', payable: '应付', counterparty: '往来方', project: '项目', bank_account: '银行账户', department: '部门', employee: '人员', CNY: '人民币' };
const MIGRATION_AMOUNT_FIELDS = new Set(['opening', 'closing', 'period_debit', 'period_credit', 'opening_debit', 'opening_credit', 'closing_debit', 'closing_credit', 'year_debit', 'year_credit', 'original_amount', 'settled_amount', 'remaining_amount', 'debit', 'credit']);
export function migrationAuxiliaryDisplay(value: unknown): { label: string; value: string }[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value).map(([kind, content]) => ({ label: MIGRATION_VALUE_LABELS[kind] ?? `原辅助项（${kind}）`, value: typeof content === 'string' ? content : JSON.stringify(content) }));
}
export function migrationFieldDisplay(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (MIGRATION_AMOUNT_FIELDS.has(key)) return migrationMoney(value);
  if (key === 'is_reversal' || key === 'posting') return value === true ? '是' : value === false ? '否' : '状态待核对';
  if (key === 'auxiliaries') return migrationAuxiliaryDisplay(value).map(item => `${item.label}：${item.value}`).join('；') || '无';
  if (key === 'auxiliary_requirements' && Array.isArray(value)) return value.map(item => MIGRATION_VALUE_LABELS[String(item)] ?? String(item)).join('、') || '无';
  if (['status', 'original_status', 'category', 'normal_balance', 'kind', 'currency', 'original_currency', 'scope'].includes(key) && typeof value === 'string') return MIGRATION_VALUE_LABELS[value] ?? value;
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
export function migrationReadableFields(payload: Record<string, unknown>): { key: string; label: string; text: string }[] {
  return Object.entries(MIGRATION_FIELD_LABELS).filter(([key]) => payload[key] !== undefined).map(([key, label]) => ({ key, label, text: migrationFieldDisplay(key, payload[key]) }));
}
export function migrationReadableLines(value: unknown): { ordinal: string; accountCode: string; description: string; debit: string; credit: string; auxiliaries: { label: string; value: string }[] }[] {
  if (!Array.isArray(value)) return [];
  return value.map(line => {
    const row = typeof line === 'object' && line !== null ? line as Record<string, unknown> : {};
    return { ordinal: typeof row.ordinal === 'number' && Number.isInteger(row.ordinal) ? String(row.ordinal) : '待核对', accountCode: String(row.account_code ?? '待核对'), description: String(row.description ?? ''), debit: migrationMoney(row.debit), credit: migrationMoney(row.credit), auxiliaries: migrationAuxiliaryDisplay(row.auxiliaries) };
  });
}
export function migrationDateError(start: string, end: string, cutover: string): string | null {
  const validMonth = (value: string) => /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
  if (!validMonth(start) || !validMonth(end)) return '请选择完整的历史起止月份。';
  if (start > end) return '历史开始月份不能晚于结束月份。';
  if (!/^\d{4}-(0[1-9]|1[0-2])-01$/.test(cutover)) return '接管日期须为目标会计期间首日。';
  if (end >= cutover.slice(0, 7)) return '历史结束月份须早于接管月份，避免新旧业务重叠。';
  const [year, month] = end.split('-').map(Number); const next = month === 12 ? `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`;
  if (next !== cutover.slice(0, 7)) return '历史结束月份须紧接接管月份，不能留下空档。';
  return null;
}
export function monthBoundary(month: string, end = false): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) throw new Error('月份格式无效。');
  if (!end) return `${month}-01`;
  const [year, value] = month.split('-').map(Number);
  // No locale/time-zone conversion enters the accounting date.
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][value - 1];
  return `${month}-${days}`;
}
export function normalizedMigrationProject(raw: MigrationProject & { status?: string }): MigrationProject { return { ...raw, state: raw.status ?? raw.state }; }
export function normalizedMigrationDetail(raw: MigrationDetail & { jobs: (MigrationJob & { phase?: string; status?: string; processed_count?: number; record_count?: number; attempt_count?: number; error_detail?: string })[] }): MigrationDetail {
  const report = raw.latest_validation;
  return { ...raw, project: normalizedMigrationProject(raw.project), files: raw.files ?? [], jobs: (raw.jobs ?? []).map(job => ({ ...job, kind: job.phase ?? job.kind, state: job.status ?? job.state, progress_done: job.processed_count ?? job.progress_done, progress_total: (job.expected_record_count ?? 0) > 0 ? job.expected_record_count : job.record_count ?? job.progress_total, attempt: job.attempt_count ?? job.attempt, error_message: job.error_detail ?? job.error_message })), latest_validation: report ? { ...report, state: report.current === false ? 'stale' : report.state ?? 'validated' } : null };
}

export const MIGRATION_COLUMNS: Record<MigrationRecordKind, { key: string; label: string; required?: boolean; aliases: string[] }[]> = {
  accounts: [
    { key: 'code', label: '科目编码', required: true, aliases: ['科目编码', '科目代码', '代码', '编码', 'code', 'account_code'] },
    { key: 'name', label: '科目名称', required: true, aliases: ['科目名称', 'name', 'account_name'] },
    { key: 'external_id', label: '原始记录 ID（无时用科目编码）', aliases: ['原始ID', '原始编号', 'external_id'] },
    { key: 'period', label: '资料期间', aliases: ['会计期间', '期间', 'period'] },
    { key: 'parent_code', label: '上级科目编码', aliases: ['上级科目', '上级科目编码', 'parent_code'] },
    { key: 'posting', label: '末级科目', required: true, aliases: ['末级科目', '是否明细科目', 'posting'] },
    { key: 'category', label: '科目类别', required: true, aliases: ['科目类别', 'category'] },
    { key: 'normal_balance', label: '余额方向', required: true, aliases: ['余额方向', 'normal_balance'] },
    { key: 'auxiliary_requirements', label: '辅助核算类型', aliases: ['辅助核算维度', '辅助核算', '辅助核算类型', 'auxiliary_requirements'] },
  ],
  auxiliaries: [
    { key: 'kind', label: '辅助类型', required: true, aliases: ['辅助类型', '档案类型', 'kind'] },
    { key: 'code', label: '辅助编码', required: true, aliases: ['辅助编码', '档案编码', '代码', '编码', 'code'] },
    { key: 'name', label: '辅助名称', required: true, aliases: ['辅助名称', '档案名称', '名称', 'name'] },
    { key: 'external_id', label: '原始记录 ID（建议保留）', aliases: ['原始ID', '原始编号', 'external_id'] },
    { key: 'period', label: '资料期间', aliases: ['会计期间', '期间', 'period'] },
  ],
  vouchers: [
    { key: 'external_id', label: '原凭证 ID', required: true, aliases: ['原凭证ID', '凭证ID', 'external_id', 'voucher_id'] },
    { key: 'date', label: '凭证日期', required: true, aliases: ['凭证日期', '日期', 'date'] },
    { key: 'period', label: '会计期间（无时由凭证日期确定）', aliases: ['会计期间', '期间', 'period'] },
    { key: 'voucher_type', label: '凭证字', required: true, aliases: ['凭证字', '凭证类别', 'voucher_type'] },
    { key: 'number', label: '凭证号', required: true, aliases: ['凭证号', 'number'] },
    { key: 'status', label: '原凭证状态', required: true, aliases: ['凭证状态', '状态', 'status'] },
    { key: 'ordinal', label: '分录序号', required: true, aliases: ['分录序号', '行号', 'ordinal'] },
    { key: 'is_reversal', label: '红字 / 冲销标志', aliases: ['红字冲销', '是否红字', 'is_reversal'] },
    { key: 'account_code', label: '科目编码', required: true, aliases: ['科目编码', 'account_code'] },
    { key: 'description', label: '摘要', required: true, aliases: ['摘要', 'description'] },
    { key: 'debit', label: '借方金额', required: true, aliases: ['借方金额', '借方', 'debit'] },
    { key: 'credit', label: '贷方金额', required: true, aliases: ['贷方金额', '贷方', 'credit'] },
    { key: 'auxiliaries', label: '辅助核算（JSON）', aliases: ['辅助核算', 'auxiliaries'] },
  ],
  balances: [
    { key: 'external_id', label: '原始余额 ID（建议保留）', aliases: ['原始ID', '原始编号', 'external_id'] },
    { key: 'period', label: '会计期间（多月必选）', aliases: ['会计期间', '期间', 'period'] },
    { key: 'account_code', label: '科目编码', required: true, aliases: ['科目编码', 'account_code'] },
    { key: 'opening', label: '期初净余额（借正贷负）', required: true, aliases: ['期初净余额', 'opening'] },
    { key: 'closing', label: '期末净余额（借正贷负）', required: true, aliases: ['期末净余额', 'closing'] },
    { key: 'opening_debit', label: '期初借方余额', aliases: ['期初借方余额', 'opening_debit'] },
    { key: 'opening_credit', label: '期初贷方余额', aliases: ['期初贷方余额', 'opening_credit'] },
    { key: 'period_debit', label: '本期借方发生额', required: true, aliases: ['本期借方发生额', '借方发生', 'period_debit'] },
    { key: 'period_credit', label: '本期贷方发生额', required: true, aliases: ['本期贷方发生额', '贷方发生', 'period_credit'] },
    { key: 'closing_debit', label: '期末借方余额', aliases: ['期末借方余额', 'closing_debit'] },
    { key: 'closing_credit', label: '期末贷方余额', aliases: ['期末贷方余额', 'closing_credit'] },
    { key: 'scope', label: '余额口径（已记账）', required: true, aliases: ['余额口径', 'scope'] },
    { key: 'year_debit', label: '本年借方累计（建议保留）', aliases: ['本年借方累计', 'year_debit'] },
    { key: 'year_credit', label: '本年贷方累计（建议保留）', aliases: ['本年贷方累计', 'year_credit'] },
    { key: 'auxiliaries', label: '辅助核算（JSON）', aliases: ['辅助核算', 'auxiliaries'] },
  ],
  open_items: [
    { key: 'external_id', label: '未结事项 ID', required: true, aliases: ['未结事项ID', 'external_id'] },
    { key: 'account_code', label: '往来科目编码', aliases: ['科目编码', 'account_code'] },
    { key: 'counterparty_code', label: '往来方编码', required: true, aliases: ['往来方编码', 'counterparty_code'] },
    { key: 'period', label: '资料期间', aliases: ['会计期间', '期间', 'period'] },
    { key: 'kind', label: '应收 / 应付类型', required: true, aliases: ['往来类型', '应收应付', 'kind'] },
    { key: 'occurred_on', label: '原业务日期', required: true, aliases: ['发生日期', '原业务日期', 'occurred_on'] },
    { key: 'original_amount', label: '原始金额', required: true, aliases: ['原始金额', 'original_amount'] },
    { key: 'settled_amount', label: '已核销金额', required: true, aliases: ['已核销金额', 'settled_amount'] },
    { key: 'remaining_amount', label: '未结金额', required: true, aliases: ['未结金额', 'remaining_amount'] },
    { key: 'description', label: '业务摘要', aliases: ['业务摘要', 'description'] },
    { key: 'due_on', label: '到期日期', aliases: ['到期日期', 'due_on'] },
    { key: 'voucher_ids', label: '关联凭证 ID（JSON）', aliases: ['关联凭证ID', 'voucher_ids'] },
  ],
};
export function suggestMigrationColumns(headers: string[], kind: MigrationRecordKind): MigrationColumnMapping {
  const columns: Record<string, number> = {};
  for (const field of MIGRATION_COLUMNS[kind]) {
    const matches = headers.map((header, i) => ({ normalized: header.trim().toLowerCase(), i })).filter(({ normalized }) => field.aliases.some(alias => alias.toLowerCase() === normalized));
    if (matches.length === 1) columns[field.key] = matches[0].i;
  }
  return { columns, date_format: 'ymd', allow_grouping: false };
}
export function migrationFields(kind: MigrationRecordKind, mapping: MigrationColumnMapping) {
  return MIGRATION_COLUMNS[kind].filter(field => kind !== 'balances' || mapping.balance_mode !== 'split' ? !['opening_debit', 'opening_credit', 'closing_debit', 'closing_credit'].includes(field.key) : !['opening', 'closing'].includes(field.key));
}
export function migrationColumnError(kind: MigrationRecordKind, mapping: MigrationColumnMapping, multiplePeriods = false): string | null {
  const missing = migrationFields(kind, mapping).filter(field => (field.required || kind === 'balances' && (multiplePeriods && field.key === 'period' || mapping.balance_mode === 'split' && ['opening_debit', 'opening_credit', 'closing_debit', 'closing_credit'].includes(field.key))) && mapping.columns[field.key] === undefined).map(field => field.label);
  if (missing.length) return `请确认对应列：${missing.join('、')}。`;
  const used = Object.values(mapping.columns).map(value => String(value));
  if (new Set(used).size !== used.length) return '同一原表列不能重复映射到多个字段。';
  if (Object.values(mapping.columns).some(value => typeof value === 'number' ? !Number.isInteger(value) || value < 0 || value > 63 : !value.trim())) return '列名或列序号无效。';
  return null;
}
export function parseCsvHeaders(text: string): string[] {
  const input = text.replace(/^\uFEFF/, ''); const out: string[] = []; let quoted = false; let value = ''; let ended = false;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (ch === '"') { if (quoted && input[i + 1] === '"') { value += '"'; i++; } else quoted = !quoted; }
    else if (!quoted && ch === ',') { out.push(value); value = ''; }
    else if (!quoted && (ch === '\n' || ch === '\r')) { out.push(value); ended = true; break; }
    else value += ch;
    if (out.length > 100) throw new Error('原表列数超出支持范围。');
  }
  if (!ended && value && !quoted) out.push(value);
  if (!out.length || quoted) throw new Error('无法识别 CSV 表头，请确认第一行含完整字段名称。');
  return out;
}
export function stableMigrationKey(keys: Record<string, string>, action: string): string { return keys[action] ||= crypto.randomUUID(); }
