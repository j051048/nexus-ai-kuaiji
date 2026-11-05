import type { ManualReviewLine } from './manual-review-types';

export type CorrectionSeed = {
  original_voucher_id: string;
  relation_kind: 'adjustment' | 'reversal';
  description: string;
  lines: ManualReviewLine[];
};
const obj = (x: unknown): x is Record<string, unknown> =>
  !!x && typeof x === 'object' && !Array.isArray(x);
const centsToMoney = (v: bigint) => `${v / 100n}.${String(v % 100n).padStart(2, '0')}`;
/** Original posting metadata is authoritative; never infer account version or auxiliary values. */
export function correctionSeed(
  raw: unknown,
  expected: { bookId: string; companyId?: string; firmId: string | null },
  kind: 'adjustment' | 'reversal',
): CorrectionSeed {
  const fail = () => new Error('原凭证范围或分录依据不完整，请重新读取，不能猜测生成更正。');
  if (
    !obj(raw) ||
    raw.posting !== 'posted' ||
    !obj(raw.voucher) ||
    !expected.firmId ||
    raw.voucher.book_id !== expected.bookId ||
    raw.voucher.firm_id !== expected.firmId ||
    (expected.companyId && raw.voucher.company_id !== expected.companyId) ||
    typeof raw.voucher.id !== 'string' ||
    !raw.voucher.id ||
    !Array.isArray(raw.lines) ||
    raw.lines.length < 2
  )
    throw fail();
  const lines = raw.lines.map((line): ManualReviewLine => {
    if (
      !obj(line) ||
      typeof line.account_id !== 'string' ||
      !line.account_id ||
      !Number.isSafeInteger(line.account_version) ||
      Number(line.account_version) < 1 ||
      !Number.isSafeInteger(line.debit_cents) ||
      Number(line.debit_cents) < 0 ||
      !Number.isSafeInteger(line.credit_cents) ||
      Number(line.credit_cents) < 0 ||
      typeof line.source_ref !== 'string' ||
      typeof line.aux_json !== 'string'
    )
      throw fail();
    let aux: unknown;
    try {
      aux = JSON.parse(line.aux_json);
    } catch {
      throw fail();
    }
    if (!obj(aux) || !Object.values(aux).every((v) => typeof v === 'string')) throw fail();
    return {
      account_id: line.account_id,
      account_version: Number(line.account_version),
      debit: kind === 'reversal' ? centsToMoney(BigInt(Number(line.credit_cents))) : '0.00',
      credit: kind === 'reversal' ? centsToMoney(BigInt(Number(line.debit_cents))) : '0.00',
      auxiliary_values: { ...aux } as Record<string, string>,
      source_ref: line.source_ref,
    };
  });
  return {
    original_voucher_id: raw.voucher.id,
    relation_kind: kind,
    description: `${kind === 'reversal' ? '冲销' : '调整'}：${String(raw.voucher.description ?? '').slice(0, 460)}`,
    lines,
  };
}
