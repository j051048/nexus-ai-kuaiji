import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { checked, identity } from './formal-books-fixtures';
import { readFileSync } from 'node:fs';

test.setTimeout(90_000);
const output = 'tmp_test/workbench-4-8-20261105';
for (const width of [1440, 390]) {
  test(`复核清单直接看证据，返回保留筛选与选择 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await identity(request, 'accountant');
    const tag = `紧凑复核-${crypto.randomUUID().slice(0, 8)}`;
    const book = await checked(
      await request.post('/api/books', { headers, data: { name: tag, period: '2026-10' } }),
    );
    await checked(
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: tag,
          rows: [
            {
              external_id: tag,
              date: '2026-10-03',
              description: tag,
              counterparty: '虚构供应商',
              amount: '128.50',
              direction: 'expense',
              evidence_ref: '仅合成清单，不是真实原件',
            },
          ],
        },
      }),
      200,
    );
    await checked(await request.post(`/api/books/${book.id}/suggestions`, { headers }), 200);
    await page.goto(`/workspace/review?book=${book.id}`);
    await page.getByLabel('复核期间', { exact: true }).fill('2026-10');
    const row = page.locator('.batch-review-row').filter({ hasText: tag });
    await expect(row).toHaveCount(1);
    await row.getByRole('checkbox').check();
    await expect(page.locator('.review-instructions')).not.toHaveAttribute('open');
    await expect(page.getByRole('complementary', { name: '批量复核预览' })).toHaveCount(0);
    await expect(page.locator('.review-list-focused')).toHaveCount(1);
    await page.screenshot({ path: `${output}/review-list-${width}.png`, fullPage: false });
    const writes: string[] = [];
    page.on('request', (r) => {
      if (r.method() !== 'GET') writes.push(new URL(r.url()).pathname);
    });
    await row.getByRole('button', { name: '查看证据', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await drawer.getByRole('button', { name: '关闭同屏复核', exact: true }).click();
    await expect(page.getByLabel('复核期间', { exact: true })).toHaveValue('2026-10');
    await expect(row.getByRole('checkbox')).toBeChecked();
    expect(writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
  test(`管理台搜索即展示结果、月度办理收起指标且返回保留展开 ${width}`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await identity(request, 'accountant');
    const tag = `精简-${crypto.randomUUID().slice(0, 8)}`;
    const company = await checked(
      await request.post('/api/companies', {
        headers,
        data: { name: `${tag}-虚构跨区域供应链管理服务有限公司`, tax_id: '' },
      }),
    );
    const book = await checked(
      await request.post('/api/books', {
        headers,
        data: { company_id: company.id, name: `${tag}-本月账套`, period: '2026-10' },
      }),
    );
    await checked(
      await request.put(`/api/books/${book.id}/service-plan`, {
        headers,
        data: {
          expected_version: 0,
          lead_user_id: 'local-accountant',
          due_on: '2026-11-08',
          reason: '仅虚构界面验收',
        },
      }),
      200,
    );
    await page.goto('/workspace/firm?period=2026-10');
    await expect(page.locator('.cockpit-summary')).toBeVisible();
    await page.screenshot({ path: `${output}/firm-${width}.png`, fullPage: false });
    const directory = page.getByRole('region', { name: '客户查找与进度', exact: true });
    await expect(directory.getByLabel('搜索服务客户')).toBeVisible();
    const before = await page.locator('.cockpit-summary-values').innerText();
    await directory.getByLabel('搜索服务客户').fill(tag);
    await directory.getByRole('button', { name: '查找', exact: true }).click();
    await expect(page.locator('.cockpit-client-details')).toHaveAttribute('open', '');
    await expect(page.locator('.cockpit-client-details .cockpit-client')).toHaveCount(1);
    await expect(page.locator('.cockpit-client-details .cockpit-client')).toBeVisible();
    expect(await page.locator('.cockpit-summary-values').innerText()).toBe(before);
    await page.screenshot({ path: `${output}/firm-search-${width}.png`, fullPage: false });
    await page.goto('/workspace/monthly?period=2026-10');
    await page.getByLabel('搜索月度客户').fill(tag);
    await page.getByRole('button', { name: '查找', exact: true }).click();
    const row = page.locator(`[data-book="${book.id}"]`);
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
    await expect(row.getByRole('heading', { name: company.name, exact: true })).toHaveCount(1);
    await expect(row.locator('.monthly-work-next')).toContainText('2026-11-08');
    await expect(row.locator('.monthly-work-next')).toContainText('核对本月服务进度');
    await expect(row.locator('.monthly-row-details')).not.toHaveAttribute('open');
    await expect(row.getByRole('button', { name: /^会计复核：/ })).not.toBeVisible();
    await expect(row.getByRole('button', { name: '继续办理', exact: true })).toBeInViewport();
    await page.screenshot({ path: `${output}/monthly-${width}.png`, fullPage: false });
    await row.getByText('查看各环节明细', { exact: true }).click();
    await expect(row.getByRole('button', { name: /^会计复核：/ })).toBeVisible();
    await row.getByRole('button', { name: '继续办理', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`book=${book.id}`));
    await page.getByRole('button', { name: '返回客户月度进度', exact: true }).click();
    await expect(page.getByLabel('搜索月度客户')).toHaveValue(tag);
    await expect(row.locator('.monthly-row-details')).toHaveAttribute('open', '');
    await row.getByText('查看各环节明细', { exact: true }).click();
    const accessibility = await new AxeBuilder({ page })
      .include('.monthly-workbench')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
      .analyze();
    expect(accessibility.violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}

test('手机缩小视口和上传断网：保留填写、显式核对再重试，不重复提交', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'employee');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
  await page.goto('/workspace/home');
  await page.getByRole('button', { name: /^提交报销/ }).click();
  const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
  await dialog.getByLabel('发生日期', { exact: true }).fill('2026-10-03');
  await dialog.getByLabel('事项金额', { exact: true }).fill('128.50');
  await dialog.getByLabel('事项摘要', { exact: true }).fill('手机断网合成报销');
  // This validates reduced visual space, not a physical iOS/Android keyboard.
  await page.setViewportSize({ width: 390, height: 520 });
  const amount = dialog.getByLabel('事项金额', { exact: true });
  await amount.click();
  await expect(amount).toBeFocused();
  await expect(amount).toBeInViewport();
  await page.screenshot({ path: `${output}/mobile-reduced-viewport.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  let uploads = 0;
  const completions: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/(complete|submit)$/.test(new URL(r.url()).pathname))
      completions.push(r.url());
  });
  await page.route('**/api/intake/drafts/*/attachments?**', async (route) => {
    uploads++;
    if (uploads === 1) await route.abort('connectionfailed');
    else await route.continue();
  });
  const attachment = {
    name: '仅虚构手机票据.png',
    mimeType: 'image/png',
    buffer: readFileSync('e2e/fixtures/v03-receipt.png'),
  };
  await dialog.getByRole('button', { name: '选择附件', exact: true }).scrollIntoViewIfNeeded();
  await expect(dialog.getByLabel('拍照上传业务附件')).toHaveAttribute('capture', 'environment');
  await dialog.getByLabel('一体化录入上传附件').setInputFiles(attachment);
  const error = dialog.getByRole('alert').filter({ hasText: '读取最新草稿' });
  await expect(error).toBeVisible();
  await expect(error).toBeInViewport();
  await expect(amount).toHaveValue('128.50');
  expect(uploads).toBe(1);
  await error.getByRole('button', { name: '读取最新草稿', exact: true }).click();
  await expect(error).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '选择附件', exact: true })).toBeEnabled();
  await expect(
    dialog.getByText('已恢复服务端草稿，请核对后继续填写', { exact: true }),
  ).toBeVisible();
  await expect(dialog.getByLabel('事项摘要', { exact: true })).toHaveValue('手机断网合成报销');
  await dialog.getByLabel('一体化录入上传附件').setInputFiles(attachment);
  await expect(dialog.getByText(attachment.name, { exact: true })).toHaveCount(1);
  expect(uploads).toBe(2);
  expect(completions).toEqual([]);
  await expect(dialog.getByRole('checkbox')).not.toBeChecked();
  await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
  const footer = dialog.locator('.intake-footer');
  await expect(footer).toBeInViewport();
  expect(
    await footer.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const hit = document.elementFromPoint(
        rect.x + rect.width / 2,
        Math.min(innerHeight - 1, rect.y + rect.height / 2),
      );
      return hit != null && node.contains(hit);
    }),
  ).toBe(true);
  await page.screenshot({ path: `${output}/mobile-upload-recovered.png` });
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
});
