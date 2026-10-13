export type MasterKind = 'counterparty' | 'project' | 'bank_account';
export const masterNames: Record<MasterKind, string> = { counterparty: '往来方', project: '项目', bank_account: '银行账户' };
export interface MasterEntity { id: string; kind: MasterKind; code: string; name: string; active: boolean; version: number }
export interface MasterAlias { id: string; kind: MasterKind; entity_id: string; original: string; status: 'pending' | 'approved' | 'rejected' | 'retired'; version: number; created_by: string; reason: string }
export interface LedgerOverview {
  id: string; name: string; book_id: string; company_id: string; period: string; can_manage: boolean; can_grant_manager: boolean; can_review: boolean;
  periods: { book_id: string; period: string; name: string; status: 'open' | 'closed'; permissions: { review: boolean } }[];
  members: { user_id: string; role: string; name: string }[];
  published_rules: { id: string; version: number; name: string }[];
  candidates: { id: string; source_rule_version: number; status: 'pending' | 'evaluated'; target_rule_id: string | null; definition: { name: string } }[];
  entities: MasterEntity[]; aliases: MasterAlias[];
}
export function nextPeriod(period: string): string {
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(period)) return '';
  const [year, month] = period.split('-').map(Number);
  return month === 12 ? year === 2099 ? '' : `${year + 1}-01` : `${year}-${String(month + 1).padStart(2, '0')}`;
}
export function canReviewAlias(alias: MasterAlias, userId: string, canReview: boolean): boolean {
  return canReview && alias.created_by !== userId && (alias.status === 'pending' || alias.status === 'approved');
}
