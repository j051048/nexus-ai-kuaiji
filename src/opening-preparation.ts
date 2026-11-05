export type OpeningAccount = {
  id: string;
  code: string;
  current_version: number;
  status: string;
  name?: string;
};
export function parseOpeningLines(text: string, accounts: OpeningAccount[]) {
  const seen = new Set<string>();
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length || lines.length > 5000) throw new Error('请粘贴 1–5000 行期初数据。');
  return lines.map((line, index) => {
    const [code, ...amounts] = line.split(/[\t,，]/).map((value) => value.trim());
    const matches = accounts.filter(
      (account) => account.code === code && account.status === 'active',
    );
    if (
      matches.length !== 1 ||
      seen.has(code) ||
      amounts.length !== 4 ||
      amounts.some((value) => !/^-?(0|[1-9]\d{0,12})(\.\d{1,2})?$/.test(value))
    )
      throw new Error(`第 ${index + 1} 行请核对科目与四列金额；同科目不可重复。`);
    seen.add(code);
    return {
      account_id: matches[0].id,
      account_version: matches[0].current_version,
      year_opening: amounts[0],
      ytd_debit: amounts[1],
      ytd_credit: amounts[2],
      opening: amounts[3],
    };
  });
}
