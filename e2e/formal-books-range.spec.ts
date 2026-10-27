import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { formalFixture, secondMonth } from './formal-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 15_000 });

for (const viewport of [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`连续期间账簿两个月只累加一次期初且如实标记未关账：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await formalFixture(request);
    await secondMonth(request, f);
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const query = `book=${f.book.id}&company=${f.company.id}&period=2026-10`;
    if (viewport.name === 'desktop') {
      await page.goto(`/workspace/accounting?${query}`);
      const tools = page.locator('.book-more-actions');
      await tools.locator('summary').click();
      await tools.getByRole('button', { name: '正式账簿', exact: true }).click();
      await expect(page).toHaveURL(/\/workspace\/formal-books\?/);
    } else {
      await page.goto(`/workspace/formal-books?${query}`);
    }
    await page.getByRole('button', { name: '连续期间账簿', exact: true }).click();
    await expect(page.getByRole('heading', { name: '连续期间账簿', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    // The second physical month must be offered as a real period, not invented by the client.
    await expect(page.getByLabel('起始月份')).toHaveValue('2026-10');
    await page.getByLabel('截止月份').selectOption('2026-11');

    const state = page.locator('.range-books-state');
    await expect(state).toContainText('不完整');
    await expect(state).toContainText(
      '2026-10 该月尚未关账：该月份尚未关账，结果不能当作已结账账簿使用',
    );
    await expect(state).toContainText(
      '2026-11 该月尚未关账：该月份尚未关账，结果不能当作已结账账簿使用',
    );
    await expect(state).toContainText('2 个月均可查，但尚未全部关账');
    const coverage = page.getByRole('table', { name: /按月覆盖/ });
    await expect(coverage.getByRole('row')).toHaveCount(3);
    await expect(coverage.getByRole('row').nth(1)).toContainText('2026-10');
    await expect(coverage.getByRole('row').nth(1)).toContainText('可用');
    await expect(coverage.getByRole('row').nth(2)).toContainText('2026-11');
    await expect(coverage.getByRole('row').nth(2)).toContainText('可用');
    // 10,000.00 opening counted once, plus 100.00 and 200.00 of range movement.
    const bank = page.getByRole('row').filter({ hasText: '1002' });
    await expect(bank).toContainText('10,000.00');
    await expect(bank).toContainText('300.00');
    await expect(bank).toContainText('10,300.00');

    await bank.getByRole('button', { name: '查看明细', exact: true }).click();
    const detail = page.locator('.range-books-detail');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText(f.tag);
    await expect(detail).toContainText(`${f.tag}-次月正式凭证`);
    await expect(detail).toContainText('10,300.00');

    const fingerprint = page.locator('.range-books-fingerprint code');
    const shown = ((await fingerprint.textContent()) ?? '').replace('…', '');
    expect(shown).toMatch(/^[0-9a-f]{16}$/);
    await page.getByRole('button', { name: '导出连续余额表 CSV', exact: true }).click();
    const downloadEvent = page.waitForEvent('download');
    const download = await downloadEvent;
    const destination = testInfo.outputPath(`range-${viewport.name}.csv`);
    await download.saveAs(destination);
    const csv = await readFile(destination, 'utf8');
    expect(csv).toContain('1002');
    expect(csv).toContain('10300.00');
    expect(csv).toContain(shown);

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    expect(errors).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`range-books-${viewport.name}.png`),
      fullPage: true,
    });

    // Range reads and exports are scoped per period: an identity outside the ledger sees nothing.
    const denied = await request.get(
      `/api/ledgers/${f.ledger.id}/trial-balance?start=2026-10&end=2026-11`,
      { headers: f.employee },
    );
    expect(denied.status()).toBe(404);
    const deniedExport = await request.get(
      `/api/ledgers/${f.ledger.id}/books/export?kind=trial_balance&start=2026-10&end=2026-11`,
      { headers: f.employee },
    );
    expect(deniedExport.status()).toBe(404);
    const backwards = await request.get(
      `/api/ledgers/${f.ledger.id}/trial-balance?start=2026-11&end=2026-10`,
      { headers: f.headers },
    );
    expect(backwards.status()).toBe(422);
  });
}
