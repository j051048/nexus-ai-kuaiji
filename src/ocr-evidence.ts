import type {
  IndependentOcrEvidence,
  IndependentOcrPage,
  OcrFieldBinding,
  OcrTextBlock,
} from './intelligence-types';

export const OCR_FIELD_LABELS: Record<string, string> = {
  invoice_number: '发票号码',
  issued_on: '开票日期',
  seller_name: '销售方',
  seller_tax_id: '销售方税号',
  buyer_name: '购买方',
  buyer_tax_id: '购买方税号',
  gross_amount: '价税合计',
  net_amount: '不含税金额',
  tax_amount: '税额',
  currency: '币种',
};

export function fieldBinding(
  evidence: IndependentOcrEvidence | undefined,
  documentIndex: number,
  field: string,
): OcrFieldBinding | undefined {
  return evidence?.bindings.find(
    (binding) => binding.document_index === documentIndex && binding.field === field,
  );
}

export function ocrScore(score: number | null | undefined): string {
  return typeof score === 'number' && Number.isFinite(score) && score >= 0 && score <= 1
    ? `${(score * 100).toFixed(1)}%`
    : '未提供';
}

export function bindingLabel(binding?: OcrFieldBinding): string {
  if (!binding || binding.status === 'unmatched') return '缺少文字证据';
  if (binding.status === 'low_confidence') return '文字识别分数偏低';
  return '已找到对应文字';
}

export function polygonPoints(block: OcrTextBlock, page: IndependentOcrPage): string | null {
  if (
    page.source !== 'paddleocr' ||
    !Number.isFinite(page.width) ||
    !Number.isFinite(page.height) ||
    page.width <= 0 ||
    page.height <= 0 ||
    block.polygon.length < 3 ||
    block.polygon.length > 16 ||
    block.polygon.some(
      (point) =>
        point.length !== 2 ||
        !point.every(Number.isFinite) ||
        point[0] < 0 ||
        point[1] < 0 ||
        point[0] > page.width ||
        point[1] > page.height,
    )
  )
    return null;
  return block.polygon.map(([x, y]) => `${x},${y}`).join(' ');
}

export function boundBlocks(page: IndependentOcrPage, binding?: OcrFieldBinding): OcrTextBlock[] {
  if (!binding || binding.status === 'unmatched' || binding.page !== page.page) return [];
  const ids = new Set(binding.block_ids);
  return page.blocks.filter((block) => ids.has(block.id));
}

export function ocrPreviewPath(eventId: string, runId: string, page: number): string {
  return `/business/events/${encodeURIComponent(eventId)}/intelligence/runs/${encodeURIComponent(runId)}/pages/${page}`;
}
