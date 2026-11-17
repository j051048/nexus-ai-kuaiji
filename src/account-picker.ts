export interface AccountChoice {
  code: string;
  name: string;
  selectable: boolean;
}

export function isLeafAccount(code: string, accounts: { code: string }[]): boolean {
  return !accounts.some(
    (account) =>
      account.code !== code &&
      account.code.startsWith(code) &&
      (account.code[code.length] === '.' || account.code.length === code.length + 2),
  );
}

function isDescendant(accountCode: string, candidate: string): boolean {
  return (
    candidate.startsWith(accountCode) &&
    candidate !== accountCode &&
    (candidate[accountCode.length] === '.' || candidate.length === accountCode.length + 2)
  );
}

export function accountChoices(
  accounts: { code: string; name: string }[],
  query: string,
): AccountChoice[] {
  const normalized = query.trim().toLocaleLowerCase();
  const matched = accounts.filter((account) => {
    if (!normalized) return true;
    return (
      account.code.toLocaleLowerCase().includes(normalized) ||
      account.name.toLocaleLowerCase().includes(normalized)
    );
  });
  const visible = new Set(matched.map((account) => account.code));
  for (const account of matched) {
    for (const parent of accounts) {
      if (isDescendant(parent.code, account.code)) visible.add(parent.code);
    }
    if (!isLeafAccount(account.code, accounts)) {
      for (const child of accounts) {
        if (isDescendant(account.code, child.code)) visible.add(child.code);
      }
    }
  }
  return accounts
    .filter((account) => visible.has(account.code))
    .map((account) => ({
      code: account.code,
      name: account.name,
      selectable: isLeafAccount(account.code, accounts),
    }))
    .sort((left, right) => {
      if (left.code === right.code) return 0;
      if (isDescendant(left.code, right.code)) return -1;
      if (isDescendant(right.code, left.code)) return 1;
      return left.code.localeCompare(right.code, 'zh-CN', { numeric: true });
    });
}

export function nearestChoiceIndex(choices: AccountChoice[], from: number, step: 1 | -1): number {
  const selectable = choices
    .map((choice, index) => (choice.selectable ? index : -1))
    .filter((index) => index >= 0);
  if (!selectable.length) return -1;
  if (from < 0) return step === 1 ? selectable[0] : selectable[selectable.length - 1];
  const position = selectable.indexOf(from);
  const next = position < 0 ? 0 : (position + step + selectable.length) % selectable.length;
  return selectable[next];
}
