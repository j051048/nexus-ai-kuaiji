export interface ClosingAccount {
  id: string;
  version: number;
  code: string;
  name: string;
}

export function closingRequestBody(bookId: string, period: string, account: ClosingAccount) {
  return {
    book_id: bookId,
    period,
    profit_account_id: account.id,
    profit_account_version: account.version,
    idempotency_key: `closing-ui-${bookId}-${period}`,
    reason: 'UI 生成期末结转草稿',
  };
}
