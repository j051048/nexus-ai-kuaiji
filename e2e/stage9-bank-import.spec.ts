import { confirmStatement, openAccounting, previewPastedStatement } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('银行稳定身份导入与人工相似交易核验保持原始来源和审核门槛', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const marker = crypto.randomUUID().slice(0, 8);
  const created = await request.post('/api/books', { headers, data: { name: `第九阶段银行身份（虚构） ${marker}`, period: '2026-10' } });
  expect(created.status()).toBe(201); const book = await created.json();
  await page.goto('/'); await openAccounting(page); await page.getByLabel('搜索企业名称').fill(marker);
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  const header = 'external_id,date,description,counterparty,amount,direction,evidence_ref,stable_transaction_id';
  async function importCsv(external: string, stable: string) {
    await previewPastedStatement(page, `${header}\n${external},2026-10-08,办公费用身份验证,虚构文具供应商,27.01,expense,虚构回执-${marker},${stable}`, '虚构银行-末四位1234');
    const result = page.waitForResponse(response => new URL(response.url()).pathname.startsWith(`/api/books/${book.id}/import-wizard/previews/`) && new URL(response.url()).pathname.endsWith('/confirm') && response.request().method() === 'POST');
    await confirmStatement(page);
    return await (await result).json();
  }
  expect((await importCsv('display-first', 'real-bank-id-1')).imported).toBe(1);
  expect((await importCsv('changed-display', 'real-bank-id-1')).duplicates).toBe(1);
  expect((await importCsv('display-second', 'real-bank-id-2')).imported).toBe(1);
  await expect(page.getByRole('button', { name: '导入流水', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '生成记账建议', exact: true }).click();

  const panel = page.getByLabel('银行交易身份与重复核验', { exact: true });
  await expect(panel).toContainText('发现 1 条');
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await panel.getByLabel('区分交易的核验依据', { exact: true }).fill('已核对银行原始编号及两份独立业务凭据');
  await panel.getByRole('button', { name: '确认两笔独立交易', exact: true }).click();
  await expect(panel).toContainText('已核验为独立交易');
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(detail.sources).toHaveLength(2); expect(detail.drafts.every((draft: { status: string }) => draft.status === 'draft')).toBe(true);
  await page.screenshot({ path: 'artifacts/stage9-bank-identity-review.png', fullPage: true });
});
