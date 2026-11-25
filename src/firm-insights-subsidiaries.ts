/** 集团驾驶舱子公司卡片归集：严格按账套/法人唯一标识区分，杜绝同名覆盖与多账套吞并。 */

export type SubsidiaryComparisonRow = {
  book_id: string;
  book_name?: string;
  company_id?: string;
  company_name: string;
  known: boolean;
  report_status: string;
  revenue_cents: number;
  expenses_cents: number;
  profit_cents: number;
  revenue_delta_cents: number | null;
  profit_delta_cents: number | null;
  cash_closing_cents: number;
};

export type MissingCompanyRow = {
  company_id: string;
  company_name: string;
  book_id?: string | null;
  owner_name: string;
  top_blocker: string;
  has_trial_draft: boolean;
  reason?: string;
};

export type RiskBookRow = {
  book_id: string;
  company_name: string;
  owner_name: string;
  blockers: { code: string; label: string; count: number }[];
};

export type SubsidiaryCardItem = {
  /** 全局唯一主键：已开账按 `book:<book_id>`，未开账法人按 `company:<company_id>` */
  id: string;
  companyId: string | null;
  bookId: string | null;
  bookName?: string;
  companyName: string;
  /** 图表与卡片用的消歧显示名：遇到同名法人或一企多账套时附加区分标签 */
  displayLabel: string;
  /** 仅在同法人多账套或同名法人时展示的副标签 */
  subLabel?: string;
  known: boolean;
  revenueCents: number;
  expensesCents: number;
  profitCents: number;
  cashCents: number;
  revenueDeltaCents: number | null;
  profitDeltaCents: number | null;
  pendingReason?: string;
  ownerName?: string;
};

function shortCode(id: string | null | undefined): string {
  if (!id) return '';
  const cleaned = id.trim();
  return cleaned.length > 6 ? cleaned.slice(-4) : cleaned;
}

export function buildSubsidiaryCards(
  companyRows: SubsidiaryComparisonRow[],
  missingCompanies: MissingCompanyRow[] = [],
  riskRows: RiskBookRow[] = [],
): SubsidiaryCardItem[] {
  const byKey = new Map<string, SubsidiaryCardItem>();
  const companyIdsWithBooks = new Set<string>();

  for (const row of companyRows) {
    const key = `book:${row.book_id}`;
    if (row.company_id) companyIdsWithBooks.add(row.company_id);
    byKey.set(key, {
      id: key,
      companyId: row.company_id ?? null,
      bookId: row.book_id,
      bookName: row.book_name,
      companyName: row.company_name,
      displayLabel: row.company_name,
      known: row.known,
      revenueCents: row.revenue_cents,
      expensesCents: row.expenses_cents,
      profitCents: row.profit_cents,
      cashCents: row.cash_closing_cents,
      revenueDeltaCents: row.revenue_delta_cents,
      profitDeltaCents: row.profit_delta_cents,
    });
  }

  for (const miss of missingCompanies) {
    const bookKey = miss.book_id ? `book:${miss.book_id}` : null;
    if (bookKey && byKey.has(bookKey)) {
      const existing = byKey.get(bookKey)!;
      existing.companyId = existing.companyId ?? miss.company_id;
      existing.pendingReason = existing.pendingReason || miss.top_blocker || miss.reason;
      existing.ownerName = existing.ownerName || miss.owner_name;
      continue;
    }
    if (!miss.book_id && companyIdsWithBooks.has(miss.company_id)) {
      continue;
    }
    const key = bookKey ?? `company:${miss.company_id}`;
    if (byKey.has(key)) continue;
    byKey.set(key, {
      id: key,
      companyId: miss.company_id,
      bookId: miss.book_id ?? null,
      companyName: miss.company_name,
      displayLabel: miss.company_name,
      known: false,
      revenueCents: 0,
      expensesCents: 0,
      profitCents: 0,
      cashCents: 0,
      revenueDeltaCents: null,
      profitDeltaCents: null,
      pendingReason:
        miss.reason === 'not_opened'
          ? '本期尚未开账（无账套，非零申报）'
          : miss.top_blocker || miss.reason || '本期尚未出复核报表',
      ownerName: miss.owner_name,
    });
  }

  for (const risk of riskRows) {
    const target = byKey.get(`book:${risk.book_id}`);
    if (!target) continue;
    target.ownerName = target.ownerName || risk.owner_name;
    if (!target.known && risk.blockers.length) {
      target.pendingReason = risk.blockers.map((b) => b.label).join('、');
    }
  }

  const items = Array.from(byKey.values());
  const companyBookCounts = new Map<string, number>();
  const nameCompanyIds = new Map<string, Set<string>>();

  for (const item of items) {
    if (item.companyId) {
      companyBookCounts.set(item.companyId, (companyBookCounts.get(item.companyId) ?? 0) + 1);
      const set = nameCompanyIds.get(item.companyName) ?? new Set<string>();
      set.add(item.companyId);
      nameCompanyIds.set(item.companyName, set);
    }
  }

  // 同法人多账套或同名不同法人：显式附加账套/法人区分标签，绝不模糊覆盖
  return items.map((item) => {
    const multiBook = item.companyId ? (companyBookCounts.get(item.companyId) ?? 0) > 1 : false;
    const duplicateName = (nameCompanyIds.get(item.companyName)?.size ?? 0) > 1;
    if (!multiBook && !duplicateName) return item;

    const parts: string[] = [];
    if (duplicateName && item.companyId) {
      parts.push(`法人 ${shortCode(item.companyId)}`);
    }
    if (multiBook) {
      parts.push(item.bookName || (item.bookId ? `账套 ${shortCode(item.bookId)}` : '未开账'));
    }
    const subLabel = parts.join(' · ');
    return {
      ...item,
      subLabel,
      displayLabel: subLabel ? `${item.companyName}（${subLabel}）` : item.companyName,
    };
  });
}
