export interface AccountingContextScope {
  book_id: string; company_id: string; company_name: string; book_name: string;
  period: string; period_status: string; draft_id: string; draft_version: number; fact_version: number | null;
}
export interface AccountingContextEvidence {
  id: string; kind: 'source' | 'attachment' | 'proposal'; label: string; detail: string;
  event_id?: string; attachment_id?: string; href?: string; created_at?: string;
}
export interface AccountingContextAction {
  type: 'review' | 'edit' | 'business' | 'export' | 'history'; label: string;
  book_id: string; draft_id: string; event_id?: string; enabled: boolean; reason?: string;
}
export interface AccountingContext {
  mode: 'evidence_summary'; scope: AccountingContextScope;
  draft: { id: string; description: string; status: string; version: number; explanation: string; issues: string[]; lines: { account_code: string; account_name: string; debit: string; credit: string; auxiliary_values?: Record<string, string> }[]; source_amount: string };
  source: { id: string; date: string; description: string; counterparty: string; amount: string; direction: string; kind: string; evidence_ref: string; external_id: string };
  evidence: AccountingContextEvidence[];
  timeline: { id: string; label: string; status: string; at: string | null; detail: string }[];
  actions: AccountingContextAction[]; suggestions: string[]; notice: string;
}
export interface AccountingContextAnswer {
  mode: 'evidence_summary'; question: string; answer: string;
  citations: { id: string; label: string; detail: string }[];
  scope: AccountingContextScope; actions: AccountingContextAction[];
}

/** Never let a late reply appear beside another enterprise or draft revision. */
export function matchesAccountingScope(scope: AccountingContextScope, bookId: string, draftId: string, draftVersion?: number): boolean {
  return scope.book_id === bookId && scope.draft_id === draftId && Number.isSafeInteger(scope.draft_version)
    && scope.draft_version > 0 && (draftVersion === undefined || scope.draft_version === draftVersion);
}
export function contextQuestion(value: string): string | null {
  const question = value.trim();
  return question.length > 0 && question.length <= 1000 ? question : null;
}
/** A model response must not nominate a different accounting target. */
export function contextActions(actions: AccountingContextAction[], scope: AccountingContextScope): AccountingContextAction[] {
  return actions.filter(action => action.book_id === scope.book_id && action.draft_id === scope.draft_id);
}
export function evidencePage(value: number | undefined): number | undefined {
  return value !== undefined && Number.isSafeInteger(value) && value > 0 && value <= 10000 ? value : undefined;
}
