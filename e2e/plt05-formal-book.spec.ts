import { expect, test } from '@playwright/test';
import { checked, formalFixture } from './formal-fixtures';

test.use({ actionTimeout: 20_000 });
test.setTimeout(90_000);

test('正式账系统随政策冻结并在报表标题区说明外部交付边界', async ({ page, request }) => {
  const f = await formalFixture(request, true, 'PLT05-A 正式账');
  const report = await checked(
    await request.post('/api/reports', {
      headers: f.headers,
      data: { book_id: f.book.id, period: '2026-10' },
    }),
  );
  expect(report.report.mode).toBe('formal');
  expect(report.report.formal_book_system).toBe('local');
  expect(report.report.formal_book_system_name).toBe('');

  const path = `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`;
  await page.goto(path);
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const close = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await close.getByLabel('本次报表版本').selectOption({ index: 1 });
  await expect(close.locator('.formal-report-mode.formal')).toContainText('正式账以本系统为准');
  await expect(close.locator('.formal-report-mode.formal')).toContainText(
    '外部交付仍需单独核验收货、导入与过账结果',
  );
  await expect(close.locator('.formal-report-mode.formal')).not.toContainText('正式账以 为准');
  await close.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/plt05-formal-book-1366.png', fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(path);
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const mobileClose = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await mobileClose.getByLabel('本次报表版本').selectOption({ index: 1 });
  await expect(mobileClose.locator('.formal-report-mode.formal')).toContainText(
    '正式账以本系统为准',
  );
  await page.screenshot({ path: 'tmp_test/plt05-formal-book-390.png', fullPage: true });
});

test('外部正式账明确以对方系统为准且不推定接收或过账', async ({ page, request }) => {
  const f = await formalFixture(request, true, 'PLT05-A 外部正式账', {
    formalBookSystem: 'external',
  });
  const report = await checked(
    await request.post('/api/reports', {
      headers: f.headers,
      data: { book_id: f.book.id, period: '2026-10' },
    }),
  );
  expect(report.report.mode).toBe('formal');
  expect(report.report.formal_book_system).toBe('external');
  expect(report.report.formal_book_system_name).toBe('畅捷通 T+');

  const path = `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`;
  await page.goto(path);
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const close = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await close.getByLabel('本次报表版本').selectOption({ index: 1 });
  const notice = close.locator('.formal-report-mode.formal');
  await expect(notice).toContainText('正式账以 畅捷通 T+ 为准');
  await expect(notice).toContainText('不代表对方已接收或过账');
  await expect(notice).not.toContainText('已正式过账');
  await page.screenshot({ path: 'tmp_test/plt05-formal-book-external-1366.png', fullPage: true });
});
