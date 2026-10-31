import type { FirmRole } from './types';

export const staffTemplates = {
  assistant: '会计助理',
  accountant: '主办会计',
  reviewer: '独立复核',
  auditor: '质控只读',
};
export type StaffTemplate = keyof typeof staffTemplates;
export function staffTemplateAllowed(role: FirmRole | null, template: StaffTemplate): boolean {
  if (template === 'reviewer') return role === 'reviewer';
  if (template === 'auditor') return ['viewer', 'operator', 'reviewer'].includes(role ?? '');
  return role === 'operator' || role === 'reviewer';
}
export type DepartmentScope = { company_id: string; department_id: string; period: string };
export function expenseSummary(value: unknown, scope: DepartmentScope): number | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (
    row.company_id !== scope.company_id ||
    row.department_id !== scope.department_id ||
    row.period !== scope.period
  )
    return null;
  return typeof row.expense_cents === 'number' && Number.isSafeInteger(row.expense_cents)
    ? row.expense_cents
    : null;
}
