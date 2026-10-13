import type { ReviewPending, ReviewSelection } from './review-types';

export const reviewTemplateLabels: Record<string, string> = {
  bank: '银行流水',
  reimbursement: '员工报销',
  procurement_expense: '采购 / 费用',
  daily_cashflow: '日常收支',
};
export function reviewGroups(items: ReviewPending[]) {
  const groups = new Map<string, { key: string; label: string; items: ReviewPending[] }>();
  for (const item of items) {
    const key = JSON.stringify([item.company_id, item.period, item.template, item.risk]);
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        label: `${item.company_name} · ${item.period} · ${reviewTemplateLabels[item.template] ?? item.template} · ${item.risk === 'basic_exception' ? '需处理基础异常' : '基础检查正常'}`,
        items: [],
      };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  return [...groups.values()];
}
export function reviewSelection(
  items: ReviewPending[],
  selected: ReadonlySet<string>,
): ReviewSelection[] {
  return items
    .filter((item) => selected.has(item.draft_id))
    .map(({ book_id, draft_id, expected_version }) => ({ book_id, draft_id, expected_version }));
}
