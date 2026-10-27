export type MonthlyPackageFile = { path: string; size_bytes: number; sha256: string };
export type MonthlyPackage = {
  id: string;
  version: number;
  book_id: string;
  company_id: string;
  period: string;
  report_id: string;
  report_version: number;
  manifest_sha256: string;
  created_at: string;
  created_by: string;
  status: 'frozen';
  contact_user_id: string;
  superseded: boolean;
  can_open: boolean;
  can_acknowledge: boolean;
  customer_opening_status: 'not_opened' | 'opened';
  customer_receipt_status: 'not_recorded' | 'acknowledged';
  external_import_status: 'separate_workflow';
  opening: null | { opened_at: string; opened_by: string };
  acknowledgement: null | { acknowledged_at: string; acknowledged_by: string; reason: string };
  files?: MonthlyPackageFile[];
  file_count?: number;
};
export type MonthlyPackageBlocker = {
  code: string;
  message: string;
  owner?: string;
  entry?: string;
};
export type MonthlyPackageOverview = {
  book_id: string;
  company_id: string;
  period: string;
  ready: boolean;
  blockers: MonthlyPackageBlocker[];
  warnings: MonthlyPackageBlocker[];
  reports: {
    id: string;
    version: number;
    status: 'published';
    current: boolean;
    content_hash: string;
  }[];
  contact: { user_id: string | null; eligible: boolean; status: string };
  permissions: { generate: boolean };
  packages: MonthlyPackage[];
};
export function validateMonthlyOverview(
  value: unknown,
  bookId: string,
): value is MonthlyPackageOverview {
  if (!value || typeof value !== 'object') return false;
  const item = value as MonthlyPackageOverview;
  return (
    item.book_id === bookId &&
    typeof item.company_id === 'string' &&
    !!item.company_id &&
    typeof item.period === 'string' &&
    /^20\d{2}-(0[1-9]|1[0-2])$/.test(item.period) &&
    typeof item.ready === 'boolean' &&
    typeof item.permissions?.generate === 'boolean' &&
    typeof item.contact?.eligible === 'boolean' &&
    (item.contact.user_id === null || typeof item.contact.user_id === 'string') &&
    ['configured', 'missing', 'ineligible'].includes(item.contact.status) &&
    Array.isArray(item.blockers) &&
    item.blockers.every((blocker) => validateBlocker(blocker)) &&
    Array.isArray(item.warnings) &&
    item.warnings.every((warning) => validateBlocker(warning)) &&
    (!item.ready || item.blockers.length === 0) &&
    Array.isArray(item.reports) &&
    item.reports.every(
      (report) =>
        report &&
        typeof report.id === 'string' &&
        !!report.id &&
        Number.isSafeInteger(report.version) &&
        report.version > 0 &&
        report.status === 'published' &&
        typeof report.current === 'boolean' &&
        typeof report.content_hash === 'string' &&
        /^[a-f0-9]{64}$/.test(report.content_hash),
    ) &&
    Array.isArray(item.packages) &&
    item.packages.every((pack) =>
      validatePackage(pack, {
        bookId,
        companyId: item.company_id,
        period: item.period,
      }),
    )
  );
}
export function validatePackage(
  value: unknown,
  scope: { bookId: string; companyId?: string; period?: string },
): value is MonthlyPackage {
  if (!value || typeof value !== 'object') return false;
  const item = value as MonthlyPackage;
  return (
    typeof item.id === 'string' &&
    /^[a-zA-Z0-9_-]{1,100}$/.test(item.id) &&
    item.book_id === scope.bookId &&
    (!scope.companyId || item.company_id === scope.companyId) &&
    typeof item.company_id === 'string' &&
    !!item.company_id &&
    (!scope.period || item.period === scope.period) &&
    /^20\d{2}-(0[1-9]|1[0-2])$/.test(item.period) &&
    Number.isSafeInteger(item.version) &&
    item.version > 0 &&
    Number.isSafeInteger(item.report_version) &&
    item.report_version > 0 &&
    typeof item.manifest_sha256 === 'string' &&
    /^[a-f0-9]{64}$/.test(item.manifest_sha256) &&
    item.status === 'frozen' &&
    typeof item.created_by === 'string' &&
    typeof item.contact_user_id === 'string' &&
    typeof item.can_acknowledge === 'boolean' &&
    typeof item.can_open === 'boolean' &&
    typeof item.superseded === 'boolean' &&
    ['not_opened', 'opened'].includes(item.customer_opening_status) &&
    (item.customer_opening_status === 'not_opened'
      ? item.opening === null
      : !!item.opening &&
        typeof item.opening.opened_at === 'string' &&
        item.opening.opened_by === item.contact_user_id) &&
    (item.files === undefined || validateFiles(item.files)) &&
    (item.file_count === undefined ||
      (Number.isSafeInteger(item.file_count) &&
        item.file_count > 0 &&
        (item.files === undefined || item.file_count === item.files.length))) &&
    ['not_recorded', 'acknowledged'].includes(item.customer_receipt_status) &&
    item.external_import_status === 'separate_workflow' &&
    (item.customer_receipt_status === 'not_recorded'
      ? item.acknowledgement === null
      : !!item.acknowledgement &&
        item.acknowledgement.acknowledged_by === item.contact_user_id &&
        typeof item.acknowledgement.acknowledged_at === 'string')
  );
}
export function validateBlocker(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const blocker = value as MonthlyPackageBlocker;
  return (
    typeof blocker.code === 'string' &&
    !!blocker.code &&
    typeof blocker.message === 'string' &&
    !!blocker.message &&
    (blocker.owner === undefined || typeof blocker.owner === 'string') &&
    (blocker.entry === undefined || typeof blocker.entry === 'string')
  );
}
export function validateFiles(files: MonthlyPackageFile[]): boolean {
  return (
    files.length > 0 &&
    files.every(
      (file) =>
        !!file &&
        typeof file.path === 'string' &&
        !!file.path &&
        !file.path.includes('..') &&
        Number.isSafeInteger(file.size_bytes) &&
        file.size_bytes >= 0 &&
        /^[a-f0-9]{64}$/.test(file.sha256),
    )
  );
}
export function packageReceiptLabel(status: MonthlyPackage['customer_receipt_status']): string {
  return status === 'acknowledged' ? '客户已签收' : '待客户签收';
}
export function packageOpeningLabel(item: MonthlyPackage): string {
  if (item.customer_receipt_status === 'acknowledged') return '客户已签收';
  if (item.customer_opening_status === 'opened') return '客户已查看 · 待签收';
  return '待客户查看';
}
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
export function canAcknowledgePackage(
  item: Pick<
    MonthlyPackage,
    'contact_user_id' | 'created_by' | 'can_acknowledge' | 'customer_receipt_status'
  >,
  userId: string,
): boolean {
  return (
    item.can_acknowledge &&
    item.contact_user_id === userId &&
    item.created_by !== userId &&
    item.customer_receipt_status === 'not_recorded'
  );
}
