import type {
  QualityLabel,
  QualityReportGroup,
  QualityStandard,
  QualityStatus,
} from './quality-types';

export const qualityModes = {
  rules: '当前企业规则',
  templates: '当前固定模板',
  stored_model: '存量分录模型',
  stored_ocr: '存量票据 OCR',
};
export const qualityStatuses: Record<QualityStatus, string> = {
  passed: '已测项一致',
  different: '有差异',
  uncovered: '未覆盖',
  stale: '证据已失效',
};
export const qualityFields: Record<string, string> = {
  gross_amount: '价税合计',
  net_amount: '不含税金额',
  tax_amount: '税额',
  seller_name: '销售方',
  buyer_name: '购买方',
  issued_on: '开票日期',
  invoice_number: '票据号码',
  seller_tax_id: '销售方税号',
  buyer_tax_id: '购买方税号',
  currency: '币种',
};
export const qualityBusinessTypes: Record<string, string> = {
  reimbursement: '报销',
  procurement_expense: '采购 / 费用',
  daily_cashflow: '日常收支',
};
export function qualityGroupRate(
  row: Pick<QualityReportGroup, 'context_stale' | 'measured_match_rate'>,
) {
  return row.context_stale
    ? '配置或证据已变化'
    : row.measured_match_rate === null
      ? '尚无已测样本'
      : `${Math.round(row.measured_match_rate * 100)}%`;
}
export function qualityEligible(row: QualityStandard) {
  return row.confirmed && !row.stale;
}
export function qualityMeasurement(value: number | null, suffix = '') {
  return value === null ? '尚未采集' : `${value}${suffix}`;
}
export function labelIdentity(
  label: Pick<QualityLabel, 'extraction_id' | 'document_index' | 'field'>,
) {
  return `${label.extraction_id}:${label.document_index}:${label.field}`;
}
export function measuredReplayRate(counts: Record<QualityStatus, number>) {
  const measured = counts.passed + counts.different;
  return measured ? `${Math.round((counts.passed / measured) * 100)}%` : '尚无已测样本';
}
