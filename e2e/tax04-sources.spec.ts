import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { checked } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [{ width: 1280, height: 1000 }, { width: 390, height: 844 }]) {
test(`TAX04 uploads a synthetic transport ledger and shows row evidence ${viewport.width}`, async ({ page, request }) => {
  test.setTimeout(120_000);
  const f = await taxFullFixture(request);
  await page.setViewportSize(viewport);
  await openTax(page, f);
  const panel = page.locator('section[aria-label="客户来源文件"]');
  await panel.scrollIntoViewIfNeeded();
  await panel.getByLabel('来源系统').fill('客户运输台账');
  await panel.getByLabel('来源批次 ID').fill('S-TAX04');
  await panel.getByLabel('版本').fill('1');
  await panel.getByLabel('页签').fill('运输明细');
  await panel.getByLabel('运输台账（xlsx）').setInputFiles('e2e/fixtures/tax04-source.xlsx');
  await panel.getByRole('button', { name: '解析并保存来源' }).click();
  await expect(panel.getByText('来源文件已解析并保存为活动版本。')).toBeVisible();
  await expect(panel.getByText(/已采用 2 行、待核对 0 行、排除 1 行/)).toBeVisible();
  await expect(panel.getByText('¥30.00 / 3.500')).toBeVisible();
  await expect(panel.getByText('L1')).toBeVisible();
  await panel.locator('tr').filter({ hasText: 'L3' }).getByRole('button', { name: '改为采用' }).click();
  await expect(panel.getByRole('alert')).toContainText('请更正原文件并上传新版本');
  await expect(panel.getByText(/已采用 2 行、待核对 0 行、排除 1 行/)).toBeVisible();
  await panel.locator('tr').filter({ hasText: 'L2' }).getByRole('button', { name: '排除', exact: true }).click();
  await expect(panel.getByText(/已采用 1 行、待核对 0 行、排除 2 行/)).toBeVisible();
  await expect(panel.getByText('来源行核定已保存，未冻结输出需要重新核对。')).toBeVisible();
  await panel.locator('tr').filter({ hasText: 'L2' }).getByRole('button', { name: '改为采用' }).click();
  await expect(panel.getByText(/已采用 2 行、待核对 0 行、排除 1 行/)).toBeVisible();
  const receiptDownload = page.waitForEvent('download');
  await panel.locator('tr').filter({ hasText: 'L1' }).getByRole('button', { name: /逐行回执/ }).click();
  const receiptFile = await receiptDownload;
  const receipt = JSON.parse(readFileSync((await receiptFile.path())!, 'utf8'));
  expect(receipt.row_external_id).toBe('L1');
  expect(receipt.receipt_fingerprint).toHaveLength(64);
  await expect(panel.getByText(/第 2 行回执已生成/)).toBeVisible();
  await panel.getByText('核对来源原件', { exact: true }).click();
  const download = page.waitForEvent('download');
  await panel.getByRole('link', { name: '下载原件' }).click();
  const original = await download;
  const path = await original.path();
  expect(path).toBeTruthy();
  expect(createHash('sha256').update(readFileSync(path!)).digest('hex')).toBe(
    createHash('sha256').update(readFileSync('e2e/fixtures/tax04-source.xlsx')).digest('hex'),
  );
  await page.screenshot({ path: `tmp_test/tax04-source-${viewport.width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test(`TAX04 every source and row remains reachable for manual handling ${viewport.width}`, async ({ page, request }) => {
  test.setTimeout(120_000);
  const f = await taxFullFixture(request);
  const content = readFileSync('e2e/fixtures/tax04-source-17rows.xlsx').toString('base64');
  const sources: { id: string; filename: string }[] = [];
  for (let index = 0; index < 6; index += 1) {
    sources.push(await checked(await request.post(`/api/tax/books/${f.book.id}/sources`, {
      headers: f.headers,
      data: { filename: `合成来源${index}.xlsx`, content_base64: content, source_system: '本地虚构运输台账',
        external_id: `paging-${index}`, version: 1, sheet_name: '运输明细', rule_version: 'tax-source-v1',
        column_map: { external_id: '行ID', date: '日期', description: '摘要', amount: '金额', quantity: '数量', target_id: '目标' } },
    })));
  }
  const listed = await checked(await request.get(`/api/tax/books/${f.book.id}/sources`, { headers: f.headers }), 200);
  const target = listed.items[5];
  expect(sources.some(item => item.id === target.id)).toBe(true);
  await page.setViewportSize(viewport);
  await openTax(page, f);
  const panel = page.locator('section[aria-label="客户来源文件"]');
  await expect(panel.getByLabel('来源文件分页', { exact: true })).toBeVisible();
  await panel.getByLabel('来源文件分页', { exact: true }).getByRole('button', { name: '下一页' }).click();
  await panel.getByRole('button').filter({ has: page.getByText(target.filename, { exact: true }) }).click();
  await panel.getByLabel('来源行分页', { exact: true }).getByRole('button', { name: '下一页' }).click();
  const last = panel.getByRole('row').filter({ hasText: 'L17' });
  await expect(last).toBeVisible();
  await last.getByRole('button', { name: '排除', exact: true }).click();
  await expect(last.getByText('已排除', { exact: true })).toBeVisible();
  await expect(panel.getByText(/已采用 16 行、待核对 0 行、排除 1 行/)).toBeVisible();
  const persisted = await checked(await request.get(`/api/tax/books/${f.book.id}/sources`, { headers: f.headers }), 200);
  const stored = persisted.items.find((item: { id: string }) => item.id === target.id);
  expect(stored.rows.find((row: { external_id: string }) => row.external_id === 'L17').status).toBe('excluded');
  expect(stored.amount_total_cents).toBe(1600);
  await page.screenshot({ path: `tmp_test/tax04-source-paging-${viewport.width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
}
