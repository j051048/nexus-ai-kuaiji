export const workspaceViews = [
  'home',
  'firm',
  'onboarding',
  'inbox',
  'observability',
  'runs',
  'business',
  'accounting',
  'review',
  'cases',
  'learning',
  'reconciliation',
  'delivery',
  'briefing',
  'ledger',
  'formal-books',
  'integration',
  'ingestion',
  'revision',
  'efficiency',
  'closing',
  'settlement',
  'quality',
  'migration',
  'catalog',
  'queue',
  'intelligence',
  'operations',
  'monthly',
  'balances',
  'recurring',
  'service',
  'materials',
  'messages',
  'portfolio',
] as const;
export type WorkspaceView = (typeof workspaceViews)[number];
/** Only these screens consume the selected accounting book in the app shell.
 * Independent task, business and review screens fetch their own current scope. */
export function workspaceUsesBook(view: WorkspaceView): boolean {
  return ![
    'home',
    'firm',
    'onboarding',
    'inbox',
    'observability',
    'runs',
    'business',
    'review',
    'cases',
    'revision',
    'operations',
    'queue',
    'intelligence',
    'monthly',
    'service',
    'materials',
    'messages',
    'portfolio',
    'ingestion',
  ].includes(view);
}

export interface WorkspaceRoute {
  view: WorkspaceView;
  bookId: string;
  companyId: string;
  period: string;
  draftId: string;
  filter: 'all' | 'pending' | 'exception' | 'approved';
  listTab: 'drafts' | 'sources' | 'exports';
  search: string;
  eventId: string;
  intakeId: string;
  caseId: string;
  batchId: string;
  jobId: string;
  migrationId: string;
  deliveryId: string;
  proposalId: string;
  reviewBookId: string;
  reviewDraftId: string;
  materialId: string;
}
const identifiers = {
  bookId: 'book',
  draftId: 'draft',
  eventId: 'event',
  intakeId: 'intake',
  caseId: 'case',
  batchId: 'batch',
  jobId: 'job',
  migrationId: 'migration',
  deliveryId: 'delivery',
  proposalId: 'proposal',
  reviewBookId: 'review_book',
  reviewDraftId: 'review_draft',
  materialId: 'material',
} as const;
export const emptyWorkspaceRoute: WorkspaceRoute = {
  view: 'home',
  bookId: '',
  companyId: '',
  period: '',
  draftId: '',
  filter: 'all',
  listTab: 'drafts',
  search: '',
  eventId: '',
  intakeId: '',
  caseId: '',
  batchId: '',
  jobId: '',
  migrationId: '',
  deliveryId: '',
  proposalId: '',
  reviewBookId: '',
  reviewDraftId: '',
  materialId: '',
};
function safeId(value: string | null): string {
  return value && value.length <= 200 && !/[\u0000-\u001f\u007f]/.test(value) ? value : '';
}
/** URL values are selection hints. Each selected entity is fetched again under
 * the authenticated API scope before its facts or attachments can be shown. */
export function readWorkspaceRoute(pathname: string, search: string): WorkspaceRoute {
  const route = { ...emptyWorkspaceRoute };
  const view = pathname.match(/^\/workspace\/([^/]+)\/?$/)?.[1];
  if (workspaceViews.includes(view as WorkspaceView)) route.view = view as WorkspaceView;
  const query = new URLSearchParams(search);
  route.companyId = safeId(query.get('company'));
  const period = query.get('period') ?? '';
  route.period = /^20\d{2}-(0[1-9]|1[0-2])$/.test(period) ? period : '';
  for (const [field, name] of Object.entries(identifiers)) {
    route[field as keyof typeof identifiers] = safeId(query.get(name));
  }
  const filter = query.get('filter');
  if (['pending', 'exception', 'approved'].includes(filter ?? ''))
    route.filter = filter as WorkspaceRoute['filter'];
  const tab = query.get('tab');
  if (['sources', 'exports'].includes(tab ?? '')) route.listTab = tab as WorkspaceRoute['listTab'];
  route.search = (query.get('search') ?? '').slice(0, 120);
  if (!route.bookId) route.draftId = '';
  if (!route.reviewBookId) route.reviewDraftId = '';
  // The shell only carries a request selection on the materials page. A stale
  // request ID on a service link must not keep route restoration unsettled.
  if (route.view !== 'materials') route.materialId = '';
  return route;
}
export function workspaceRouteUrl(
  route: WorkspaceRoute,
  metadata?: { companyId?: string; period?: string },
  existingSearch = '',
): string {
  // Preserve only known page selectors and non-sensitive filters. Never retain
  // arbitrary query strings, tokens, uploaded content or accounting facts.
  const previous = new URLSearchParams(existingSearch);
  const query = new URLSearchParams();
  // Exact package links and the tax subview belong to one selected book only.
  // Never carry them when a user switches clients or leaves delivery work.
  if (route.bookId && previous.get('book') === route.bookId) {
    if (route.view === 'briefing' || route.view === 'delivery') {
      // Keep malformed/duplicate explicit targets for the panel to reject.
      // Dropping them would silently turn a bad link into "open latest".
      for (const target of previous.getAll('package')) query.append('package', target);
    }
    if (route.view === 'delivery' && previous.get('section') === 'tax') query.set('section', 'tax');
  }
  for (const name of [
    'review_company',
    'review_period',
    'review_template',
    'review_risk',
    'review_offset',
  ]) {
    const value = previous.get(name);
    if (value && route.view === 'review') query.set(name, value.slice(0, 200));
  }
  for (const [field, name] of Object.entries(identifiers)) {
    const value = route[field as keyof typeof identifiers];
    if (value) query.set(name, value);
  }
  if (route.filter !== 'all') query.set('filter', route.filter);
  if (route.listTab !== 'drafts') query.set('tab', route.listTab);
  if (route.search) query.set('search', route.search);
  if (route.companyId) query.set('company', route.companyId);
  if (route.period) query.set('period', route.period);
  if (route.bookId && metadata?.companyId) query.set('company', metadata.companyId);
  if (route.bookId && metadata?.period) query.set('period', metadata.period);
  const suffix = query.toString();
  return `/workspace/${route.view}${suffix ? '?' + suffix : ''}`;
}
export function workspaceRouteKey(route: WorkspaceRoute): string {
  return workspaceRouteUrl(route);
}
