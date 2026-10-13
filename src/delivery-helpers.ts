import { uploadSizeError } from './transport-limits';

export const deliveryStates = { verified: '回执数据一致', received_unverified: '已收到 · 待核验', discrepancy: '存在交付差异' };
const labels: Record<string, string> = {
  book_id_mismatch: '客户账套不一致', company_id_mismatch: '客户企业不一致', period_mismatch: '会计期间不一致', currency_mismatch: '币种不一致',
  date_mismatch: '凭证日期不一致', amount_mismatch: '凭证金额不一致', description_mismatch: '摘要不一致',
  duplicate_draft_id: '回执重复列出同一凭证', duplicate_external_voucher_id: '同一回执重复使用外部凭证号', unknown_draft_id: '凭证不属于本次导出', legacy_manifest_missing: '旧导出历史清单不完整',
  line_set_mismatch: '分录行数或序号不一致', line_account_code_mismatch: '分录科目不一致', line_debit_mismatch: '分录借方金额不一致', line_credit_mismatch: '分录贷方金额不一致', line_auxiliary_values_mismatch: '分录辅助核算不一致',
  external_rejected: '客户软件报告拒绝', external_voucher_reused: '外部凭证号已用于其他凭证', multiple_external_vouchers: '同一凭证出现不同外部凭证号',
  receipt_item_missing: '本次回执未列出此凭证', external_status_unknown: '客户软件结果尚未明确', external_voucher_id: '缺少外部凭证号',
  book_id: '缺少账套标识', company_id: '缺少企业标识', period: '缺少会计期间', currency: '缺少币种', date: '缺少日期', amount: '缺少凭证金额', description: '缺少摘要',
  lines: '缺少逐行分录', line_ordinal: '缺少分录序号', line_account_code: '缺少分录科目', line_debit: '缺少借方金额', line_credit: '缺少贷方金额', line_auxiliary_values: '缺少辅助核算字段',
};
export function deliveryIssueLabel(code: string): string { return labels[code] ?? code; }
export function receiptFileFormat(name: string): 'json' | 'csv' {
  const extension = name.toLowerCase().split('.').pop();
  if (extension !== 'json' && extension !== 'csv') throw new Error('请选择 UTF-8 编码的 JSON 或 CSV 回执文件。');
  return extension;
}
export function validateReceiptFile(name: string, bytes: number): 'json' | 'csv' {
  const invalidSize = uploadSizeError(bytes, 2_000_000);
  if (invalidSize) throw new Error(`回执${invalidSize}`);
  return receiptFileFormat(name);
}
export function decodeReceiptBytes(bytes: ArrayBuffer): string {
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new Error('回执文件须使用有效 UTF-8 编码。'); }
}
export function deliveryTime(value: string): string { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }); }
