import type {
  CatalogAccount,
  CatalogAccountInput,
  CatalogTemplate,
  CatalogTemplateInput,
} from './catalog-types';

export const auxiliaryLabels = {
  department: '部门',
  counterparty: '交易对方',
  employee: '人员',
  project: '项目',
};
export const categoryLabels = {
  asset: '资产',
  liability: '负债',
  equity: '权益',
  cost: '成本',
  income: '收入',
  expense: '费用',
  other: '其他',
};
export const businessTypeLabels = {
  reimbursement: '员工报销',
  procurement_expense: '采购 / 费用',
  daily_cashflow: '日常收支',
};

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function allowedFields(row: Record<string, unknown>, fields: string[]): void {
  if (Object.keys(row).some((key) => !fields.includes(key)))
    throw new Error('包含不支持的字段，请使用页面提供的格式。');
}
function parseJSON(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('JSON 格式不正确，请检查双引号、逗号与括号。');
  }
}

export function parseAccountImport(text: string): CatalogAccountInput[] {
  const value: unknown = parseJSON(text);
  if (!Array.isArray(value) || value.length < 1 || value.length > 1000)
    throw new Error('科目 JSON 必须是 1 至 1000 项的数组。');
  const codes = new Set<string>();
  for (const row of value) {
    if (!object(row)) throw new Error('每项科目必须是 JSON 对象。');
    allowedFields(row, [
      'code',
      'name',
      'category',
      'normal_balance',
      'active',
      'auxiliary_requirements',
      'expected_version',
    ]);
    if (
      typeof row.code !== 'string' ||
      !/^[A-Za-z0-9][A-Za-z0-9._-]{0,29}$/.test(row.code) ||
      typeof row.name !== 'string' ||
      !row.name.trim() ||
      row.name.length > 120
    )
      throw new Error('请填写有效科目代码与名称。');
    if (codes.has(row.code)) throw new Error('科目代码重复，请合并后再导入。');
    codes.add(row.code);
    if (
      row.expected_version !== undefined &&
      row.expected_version !== null &&
      (!Number.isInteger(row.expected_version) || Number(row.expected_version) < 1)
    )
      throw new Error('已有科目必须带有效的版本编号 expected_version。');
  }
  return value as CatalogAccountInput[];
}

export function chartImportText(accounts: CatalogAccount[]): string {
  return JSON.stringify(
    accounts.map(({ version, ...account }) => ({ ...account, expected_version: version })),
    null,
    2,
  );
}

export function templateText(template?: CatalogTemplate): string {
  if (template) {
    const { id: _id, version: _version, ...definition } = template;
    return JSON.stringify(definition, null, 2);
  }
  return JSON.stringify(
    {
      name: '已付款办公费用',
      business_type: 'procurement_expense',
      direction: 'expense',
      category: 'office',
      payment_status: 'paid',
      active: true,
      lines: [
        { account_code: '6602.02', side: 'debit', amount_field: 'gross' },
        { account_code: '1002', side: 'credit', amount_field: 'gross' },
      ],
    },
    null,
    2,
  );
}

export function parseTemplate(text: string): CatalogTemplateInput {
  const row: unknown = parseJSON(text);
  if (!object(row)) throw new Error('模板必须是 JSON 对象。');
  allowedFields(row, [
    'name',
    'business_type',
    'direction',
    'category',
    'payment_status',
    'active',
    'lines',
  ]);
  if (
    typeof row.name !== 'string' ||
    !row.name.trim() ||
    !['reimbursement', 'procurement_expense', 'daily_cashflow'].includes(
      String(row.business_type),
    ) ||
    !['expense', 'income'].includes(String(row.direction))
  )
    throw new Error('请填写模板名称、业务类型与收支方向。');
  if (!Array.isArray(row.lines) || row.lines.length < 2 || row.lines.length > 20)
    throw new Error('模板需要 2 至 20 行固定分录。');
  for (const line of row.lines) {
    if (!object(line)) throw new Error('每行分录必须是 JSON 对象。');
    allowedFields(line, ['account_code', 'side', 'amount_field']);
    if (
      typeof line.account_code !== 'string' ||
      !line.account_code ||
      !['debit', 'credit'].includes(String(line.side)) ||
      !['gross', 'net', 'tax'].includes(String(line.amount_field))
    )
      throw new Error('分录仅支持 debit/credit 与 gross/net/tax，不接受表达式。');
  }
  return row as unknown as CatalogTemplateInput;
}
