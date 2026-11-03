import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { openTax, reviewTaxPaper, taxFullFixture, taxPaper } from './n04-tax-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 15_000 });

test('N05 真实浏览器登记回执、下载原件并保留更正历史', async ({ page, request }, testInfo) => {
  const f = await taxFullFixture(request);
  const paper = await taxPaper(request, f);
  await reviewTaxPaper(request, f, paper);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1280, height: 900 });
  const panel = await openTax(page, f);
  const section = panel.getByRole('region', { name: '外部办理记录', exact: true });
  await expect(section).toContainText('尚未登记外部办理结果');

  await section.getByRole('combobox', { name: '税种', exact: true }).selectOption('vat');
  await section.getByRole('combobox', { name: '办理结果', exact: true }).selectOption('filed');
  await section.getByRole('textbox', { name: '回执或流水号', exact: true }).fill('N05-REF-0001');
  await section.getByRole('textbox', { name: '办理日期', exact: true }).fill('2026-10-20');
  await section.getByRole('textbox', { name: '缴款金额（元）', exact: true }).fill('30.00');
  await section.getByRole('textbox', { name: '证据说明', exact: true }).fill('本地虚构税局回执，仅供界面验收');
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUqYhiYGBgYgADAAqsAOodif7xAAAAAElFTkSuQmCC',
    'base64',
  );
  await section.locator('input[type=file]').setInputFiles({
    name: 'N05虚构税局回执.png',
    mimeType: 'image/png',
    buffer: png,
  });
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/api/tax/filings') && r.request().method() === 'POST',
  );
  const attached = page.waitForResponse(
    (r) => r.url().includes('/attachments') && r.request().method() === 'POST',
  );
  await section.getByRole('button', { name: '登记首次登记结果', exact: true }).click();
  expect((await created).status()).toBe(201);
  expect((await attached).status()).toBe(201);

  const row = section.locator('li').filter({ hasText: 'N05-REF-0001' });
  await expect(row).toContainText('增值税 · 已申报 · 首次登记 · 当前');
  await expect(row).toContainText('底稿版本 v1');
  await expect(row).toContainText('金额核对 一致');
  await expect(row).toContainText('N05虚构税局回执.png');

  const downloaded = page.waitForEvent('download');
  const fetched = page.waitForResponse(
    (r) => r.url().includes('/api/tax/filings/') && r.request().method() === 'GET',
  );
  await row.getByRole('button', { name: /下载原件/ }).click();
  const [response, download] = await Promise.all([fetched, downloaded]);
  expect(response.status()).toBe(200);
  const path = testInfo.outputPath('n05-receipt.png');
  await download.saveAs(path);
  const bytes = readFileSync(path);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(
    (await response.allHeaders())['x-content-sha256'],
  );
  expect(download.suggestedFilename()).toBe('N05虚构税局回执.png');
  await page.screenshot({ path: testInfo.outputPath('n05-registered.png'), fullPage: true });

  await row.getByRole('button', { name: '更正此回执', exact: true }).click();
  await section.getByRole('textbox', { name: '回执或流水号', exact: true }).fill('N05-REF-0002');
  await section.getByRole('textbox', { name: '办理日期', exact: true }).fill('2026-10-21');
  await section
    .getByRole('textbox', { name: '更正原因（必填，3–1000 字）', exact: true })
    .fill('本地虚构更正：原流水号登记笔误，原回执保留');
  const corrected = page.waitForResponse(
    (r) => r.url().endsWith('/api/tax/filings') && r.request().method() === 'POST',
  );
  await section.getByRole('button', { name: '登记更正结果', exact: true }).click();
  expect((await corrected).status()).toBe(201);
  const history = section.locator('li');
  await expect(history.filter({ hasText: 'N05-REF-0001' })).toContainText('历史');
  await expect(history.filter({ hasText: 'N05-REF-0002' })).toContainText('当前');
  await page.screenshot({ path: testInfo.outputPath('n05-corrected.png'), fullPage: true });
  expect(errors).toEqual([]);
});
