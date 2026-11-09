import type { EfficiencyPortfolioPage } from './EfficiencyPortfolio';

/** Validate the complete page before displaying any enterprise measurements. */
export function parseEfficiencyPortfolio(
  raw: unknown,
  request: { period: string; limit: number; offset: number },
): EfficiencyPortfolioPage {
  const fail = () => {
    throw new Error('人工投入记录的范围或统计不完整，请重新读取；不会推算缺失工时。');
  };
  const object = (v: unknown): Record<string, unknown> =>
    v !== null && typeof v === 'object' && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : fail();
  const text = (v: unknown): string => (typeof v === 'string' && v.trim() ? v : fail());
  const number = (v: unknown): number =>
    typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : fail();
  const array = (v: unknown): unknown[] => (Array.isArray(v) ? v : fail());
  const counts = (v: Record<string, unknown>) => {
    const session_count = number(v.session_count),
      draft_count = number(v.draft_count),
      reviewed_drafts = number(v.reviewed_drafts);
    const credited_seconds = v.credited_seconds === null ? null : number(v.credited_seconds);
    if (reviewed_drafts > draft_count || (session_count === 0) !== (credited_seconds === null))
      fail();
    return { session_count, draft_count, reviewed_drafts, credited_seconds };
  };
  const root = object(raw),
    page = object(root.pagination);
  if (
    root.period !== request.period ||
    root.measurement !== 'explicit_book_sessions' ||
    page.limit !== request.limit ||
    page.offset !== request.offset ||
    typeof page.has_more !== 'boolean'
  )
    fail();
  const seenCompanies = new Set<string>(),
    seenBooks = new Set<string>();
  const items = array(root.items).map((value) => {
    const item = object(value),
      company_id = text(item.company_id);
    if (item.period !== request.period || seenCompanies.has(company_id)) fail();
    seenCompanies.add(company_id);
    const books = array(item.books).map((value) => {
      const book = object(value),
        book_id = text(book.book_id);
      if (seenBooks.has(book_id)) fail();
      seenBooks.add(book_id);
      return { book_id, name: text(book.name), ...counts(book) };
    });
    if (!books.length) fail();
    const total = counts(item);
    for (const field of ['session_count', 'draft_count', 'reviewed_drafts'] as const)
      if (total[field] !== books.reduce((sum, b) => sum + b[field], 0)) fail();
    if (
      total.credited_seconds !== null &&
      total.credited_seconds !== books.reduce((sum, b) => sum + (b.credited_seconds ?? 0), 0)
    )
      fail();
    return {
      company_id,
      company_name: text(item.company_name),
      period: request.period,
      books,
      ...total,
    };
  });
  const total = number(page.total);
  if (
    items.length > request.limit ||
    (items.length > 0 && request.offset + items.length > total) ||
    page.has_more !== request.offset + items.length < total
  )
    fail();
  return {
    period: request.period,
    notice: text(root.notice),
    items,
    pagination: {
      limit: request.limit,
      offset: request.offset,
      total,
      has_more: page.has_more as boolean,
    },
  };
}
