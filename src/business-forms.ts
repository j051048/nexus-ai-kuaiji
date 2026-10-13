import type { BusinessPayload, BusinessTemplate } from './business-types';
import { moneyToCents } from './csv';
import { DEFAULT_ATTACHMENT_BYTES, uploadSizeError } from './transport-limits';

export const BUSINESS_TEMPLATES = [
  { id: 'reimbursement' as const, name: '员工报销', description: '差旅、办公与日常垫付费用', icon: 'receipt' },
  { id: 'procurement_expense' as const, name: '采购 / 费用', description: '采购、租金与企业费用事项', icon: 'purchase' },
  { id: 'daily_cashflow' as const, name: '日常收支', description: '已发生的经营收入与支出', icon: 'cashflow' },
];
export const BUSINESS_CATEGORIES = { office: '办公费用', travel: '差旅费用', rent: '租赁费用', communication: '通信费用', other: '其他事项' };
export const ATTACHMENT_TYPES = ['application/pdf', 'image/png', 'image/jpeg', 'image/webp'];
export const MAX_ATTACHMENT_BYTES = DEFAULT_ATTACHMENT_BYTES;
export const MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024;

export function initialBusinessPayload(template: BusinessTemplate): BusinessPayload {
  return { occurred_on: new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date()), description: '', counterparty: '', amount: '', direction: 'expense', payment_status: template === 'daily_cashflow' ? 'paid' : 'unpaid', category: 'other', purpose: '', project: '' };
}

export function normalizeBusinessPayload(payload: BusinessPayload): BusinessPayload {
  const cents = moneyToCents(payload.amount);
  return { ...payload, amount: `${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`, description: payload.description.trim(), counterparty: payload.counterparty.trim(), purpose: payload.purpose?.trim() ?? '', project: payload.project?.trim() ?? '' };
}

export function validateBusinessPayload(template: BusinessTemplate, payload: BusinessPayload): string {
  const date = new Date(`${payload.occurred_on}T00:00:00Z`);
  if (!/^20\d{2}-\d{2}-\d{2}$/.test(payload.occurred_on) || !Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== payload.occurred_on) return '请选择 2000–2099 年内有效的事项发生日期。';
  if (!payload.description.trim() || payload.description.length > 500) return '摘要须为 1–500 个字符。';
  if (!payload.counterparty.trim() || payload.counterparty.length > 200) return '请填写交易对方（不超过 200 个字符）。';
  if (!/^(0|[1-9]\d{0,11})(\.\d{1,2})?$/.test(payload.amount) || !/[1-9]/.test(payload.amount)) return '金额须为大于零的人民币金额，最多保留两位小数。';
  if (template === 'reimbursement' && (payload.direction !== 'expense' || payload.payment_status !== 'unpaid')) return '员工报销须为待企业付款的支出事项。';
  if (template === 'procurement_expense' && payload.direction !== 'expense') return '采购 / 费用模板只支持支出事项。';
  if (template === 'daily_cashflow' && payload.payment_status !== 'paid') return '日常收支须为已经收付的事项。';
  if ((payload.purpose?.length ?? 0) > 1000 || (payload.project?.length ?? 0) > 200) return '业务用途或项目名称超过长度限制。';
  return '';
}

export function validateAttachment(file: Pick<File, 'size' | 'type' | 'name'>, existingCount: number, existingBytes: number): string {
  if (!ATTACHMENT_TYPES.includes(file.type)) return '附件仅支持 PDF、PNG、JPEG 和 WebP。';
  const sizeError = uploadSizeError(file.size, MAX_ATTACHMENT_BYTES);
  if (sizeError) return sizeError;
  if (existingCount >= 10) return '每个事项最多上传 10 个附件。';
  if (existingBytes + file.size > MAX_TOTAL_ATTACHMENT_BYTES) return '每个事项的附件总大小不得超过 50 MB。';
  return '';
}
