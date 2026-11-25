import { describe, expect, it } from 'vitest';
import {
  buildSubsidiaryCards,
  type MissingCompanyRow,
  type SubsidiaryComparisonRow,
} from './firm-insights-subsidiaries';

function row(overrides: Partial<SubsidiaryComparisonRow>): SubsidiaryComparisonRow {
  return {
    book_id: 'book-1',
    book_name: '主账套',
    company_id: 'comp-1',
    company_name: '甲公司',
    known: true,
    report_status: 'reviewed',
    revenue_cents: 100_00,
    expenses_cents: 60_00,
    profit_cents: 40_00,
    revenue_delta_cents: null,
    profit_delta_cents: null,
    cash_closing_cents: 500_00,
    ...overrides,
  };
}

describe('集团驾驶舱子公司卡片归集（唯一标识防覆盖）', () => {
  it('两个同名不同法人各自独立保留，不发生后者覆盖前者', () => {
    const cards = buildSubsidiaryCards([
      row({
        book_id: 'book-a',
        company_id: 'comp-alpha-01',
        company_name: '云链科技',
        revenue_cents: 1000_00,
      }),
      row({
        book_id: 'book-b',
        company_id: 'comp-beta-02',
        company_name: '云链科技',
        revenue_cents: 2500_00,
      }),
    ]);

    expect(cards).toHaveLength(2);
    expect(cards.map((c) => c.bookId)).toEqual(['book-a', 'book-b']);
    expect(cards[0].revenueCents).toBe(1000_00);
    expect(cards[1].revenueCents).toBe(2500_00);
    expect(cards[0].displayLabel).not.toBe(cards[1].displayLabel);
    expect(cards[0].subLabel).toContain('法人');
  });

  it('同一法人下存在两套账时分别保留，并展示各自账套名与精确跳转标识', () => {
    const cards = buildSubsidiaryCards([
      row({
        book_id: 'book-main',
        book_name: '主体账套',
        company_id: 'comp-1',
        company_name: '长风物业',
        revenue_cents: 800_00,
      }),
      row({
        book_id: 'book-branch',
        book_name: '食堂独立账套',
        company_id: 'comp-1',
        company_name: '长风物业',
        known: false,
        report_status: 'missing',
        revenue_cents: 0,
      }),
    ]);

    expect(cards).toHaveLength(2);
    expect(cards[0].id).toBe('book:book-main');
    expect(cards[1].id).toBe('book:book-branch');
    expect(cards[0].subLabel).toBe('主体账套');
    expect(cards[1].subLabel).toBe('食堂独立账套');
    expect(cards[0].known).toBe(true);
    expect(cards[1].known).toBe(false);
  });

  it('未开账缺报法人与有账套缺报法人均完整呈现在分母中，且不按零申报处理', () => {
    const missing: MissingCompanyRow[] = [
      {
        company_id: 'comp-missing-1',
        company_name: '新设商贸公司',
        book_id: null,
        owner_name: '待安排',
        top_blocker: '本期未开账',
        has_trial_draft: false,
        reason: 'not_opened',
      },
      {
        company_id: 'comp-1',
        company_name: '甲公司',
        book_id: 'book-1',
        owner_name: '李会计',
        top_blocker: '报表尚未复核',
        has_trial_draft: true,
        reason: 'report_missing',
      },
    ];
    const cards = buildSubsidiaryCards(
      [row({ book_id: 'book-1', company_id: 'comp-1', company_name: '甲公司', known: false })],
      missing,
    );

    expect(cards).toHaveLength(2);
    const openedPending = cards.find((c) => c.bookId === 'book-1');
    const notOpened = cards.find((c) => c.companyId === 'comp-missing-1');
    expect(openedPending?.ownerName).toBe('李会计');
    expect(openedPending?.pendingReason).toContain('报表尚未复核');
    expect(notOpened?.bookId).toBeNull();
    expect(notOpened?.known).toBe(false);
    expect(notOpened?.pendingReason).toContain('非零申报');
  });

  it('合法零收入已复核公司保持 known=true，与缺报和加载失败语义严格区分', () => {
    const cards = buildSubsidiaryCards([
      row({
        book_id: 'book-zero',
        company_id: 'comp-zero',
        company_name: '筹建子公司',
        known: true,
        revenue_cents: 0,
        expenses_cents: 0,
        profit_cents: 0,
        cash_closing_cents: 0,
      }),
    ]);

    expect(cards).toHaveLength(1);
    expect(cards[0].known).toBe(true);
    expect(cards[0].revenueCents).toBe(0);
  });
});
