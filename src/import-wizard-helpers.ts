import type { ImportField, ImportMapping, ImportProfile } from './import-wizard-types';

export const IMPORT_FIELD_LABELS: Record<ImportField, string> = {
  external_id: '流水编号',
  stable_transaction_id: '银行原始交易号',
  date: '交易日期',
  description: '摘要 / 用途',
  counterparty: '交易对方',
  amount: '金额',
  direction: '收入 / 支出方向',
  income: '收入金额',
  expense: '支出金额',
  evidence_ref: '凭据引用（选填）',
};
export const IMPORT_TEMPLATE =
  '交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用\r\nDEMO-001,PERIOD-01,办公用品采购,示例文具店,368.50,支出,示例发票001\r\nDEMO-002,PERIOD-02,服务收入,示例客户,1800.00,收入,示例合同002\r\n';

export function mappingFields(mode: ImportMapping['amount_mode']): ImportField[] {
  return [
    'date',
    'description',
    'counterparty',
    'external_id',
    'stable_transaction_id',
    ...(mode === 'split'
      ? (['income', 'expense'] as ImportField[])
      : mode === 'direction'
        ? (['amount', 'direction'] as ImportField[])
        : (['amount'] as ImportField[])),
    'evidence_ref',
  ];
}

export function mappingError(mapping: ImportMapping, headers: string[]): string | null {
  const required: ImportField[] = [
    'date',
    'description',
    ...(mapping.amount_mode === 'split'
      ? (['income', 'expense'] as ImportField[])
      : mapping.amount_mode === 'direction'
        ? (['amount', 'direction'] as ImportField[])
        : (['amount'] as ImportField[])),
  ];
  const missing = required.filter((field) => mapping.columns[field] === undefined);
  if (missing.length)
    return `请对应${missing.map((field) => IMPORT_FIELD_LABELS[field]).join('、')}。`;
  const values = Object.values(mapping.columns);
  if (
    values.some(
      (value) =>
        value === undefined || !Number.isInteger(value) || value < 0 || value >= headers.length,
    )
  )
    return '字段列号超出文件范围。';
  if (new Set(values).size !== values.length) return '一列只能对应一个字段，请检查重复映射。';
  return null;
}

export function compatibleProfile(profile: ImportProfile, headers: string[]): boolean {
  return (
    profile.headers.length === headers.length &&
    profile.headers.every((header, i) => header === headers[i])
  );
}

export function changeAmountMode(
  mapping: ImportMapping,
  mode: ImportMapping['amount_mode'],
): ImportMapping {
  const fields = new Set(mappingFields(mode));
  return {
    ...mapping,
    amount_mode: mode,
    columns: Object.fromEntries(
      Object.entries(mapping.columns).filter(([key]) => fields.has(key as ImportField)),
    ),
  };
}
