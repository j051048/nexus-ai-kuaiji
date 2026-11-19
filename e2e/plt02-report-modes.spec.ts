import { expect, test, type APIResponse } from '@playwright/test';
import { checked, formalFixture } from './formal-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 20_000 });

async function json(response: APIResponse) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

test('试算报表在界面阻断复核与预检，政策页可批准但不会把旧试算改成正式', async ({
  page,
  request,
}) => {
  const f = await formalFixture(request, false, 'PLT02 试算虚构');
  const synthetic = await checked(
    await request.post('/api/opening/policies', {
      headers: f.headers,
      data: {
        ledger_id: f.ledger.id,
        standard_kind: 'small_enterprise',
        taxpayer_kind: 'general',
        formal_book_system: 'local',
        effective_from: '2026-10',
        effective_to: '2026-10',
        is_synthetic: true,
        reason: 'PLT02-C 合成政策试算浏览器验收',
      },
    }),
  );
  const report = await checked(
    await request.post('/api/reports', {
      headers: f.headers,
      data: { book_id: f.book.id, period: '2026-10' },
    }),
  );
  expect(report.report.mode).toBe('trial');
  expect(report.report.trial_reasons.join('；')).toContain('尚未批准');

  const reviewerSession = await json(
    await request.get('/api/session?persona=reviewer'),
  );
  const grantReviewer = await request.put('/api/firm/members', {
    headers: f.headers,
    data: { email: 'reviewer@demo.example.invalid', role: 'admin' },
  });
  expect(grantReviewer.status(), await grantReviewer.text()).toBe(200);

  const path = `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`;
  await page.goto(path);
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const close = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await close.getByLabel('本次报表版本').selectOption({ index: 1 });
  await expect(close.locator('.formal-report-mode.trial')).toBeVisible();
  await expect(close.locator('.formal-report-mode.trial')).toContainText('试算，不可对外');
  await expect(close.locator('.formal-report-mode.trial')).toContainText('尚未批准');
  await expect(close.getByRole('button', { name: '核验报表勾稽', exact: true })).toBeDisabled();
  await expect(close.getByRole('button', { name: '生成月结预检', exact: true })).toBeDisabled();
  await close.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/plt02c-trial-1366.png', fullPage: true });

  await page.getByRole('button', { name: '返回账簿（保留筛选）', exact: true }).click();
  await page
    .getByText('开账准备（核对模板、科目、子账、银行、期初与岗位分工）', { exact: true })
    .click();
  await page.getByRole('button', { name: '查看核算政策', exact: true }).click();
  const opening = page.getByRole('region', { name: '期初办理', exact: true });
  await expect(opening.locator('.policy-list')).toContainText('合成演练政策');
  await expect(opening.locator('.policy-list')).toContainText('未批准');
  await expect(opening.getByRole('button', { name: '批准本政策', exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(path);
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  const mobileClose = page.getByRole('region', { name: '正式月结与报表发布', exact: true });
  await mobileClose.getByLabel('本次报表版本').selectOption({ index: 1 });
  await expect(mobileClose.locator('.formal-report-mode.trial')).toBeVisible();
  await page.screenshot({ path: 'tmp_test/plt02c-trial-390.png', fullPage: true });

  // Only another firm admin can approve; the browser switches to the real accountant identity.
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.route('**/api/session', (route) =>
    route.fulfill({
      json: reviewerSession,
    }),
  );
  await page.goto(path);
  await page
    .getByText('开账准备（核对模板、科目、子账、银行、期初与岗位分工）', { exact: true })
    .click();
  await page.getByRole('button', { name: '查看核算政策', exact: true }).click();
  const approverOpening = page.getByRole('region', { name: '期初办理', exact: true });
  await expect(
    approverOpening.getByRole('button', { name: '批准本政策', exact: true }),
  ).toBeVisible();
  await approverOpening.getByRole('button', { name: '批准本政策', exact: true }).click();
  await expect(approverOpening.locator('.policy-list')).toContainText('批准人');
  await expect(approverOpening.locator('.policy-list')).not.toContainText('local-accountant');
  await expect(approverOpening.locator('.policy-list')).toContainText('与批准时一致');
  await expect(
    approverOpening.getByRole('button', { name: '批准本政策', exact: true }),
  ).toHaveCount(0);
  await approverOpening.screenshot({ path: 'tmp_test/plt02c-policy-approved-1366.png' });

  // Approving the policy must not retroactively promote an already generated trial.
  const stillTrial = await json(
    await request.get(`/api/reports?book_id=${f.book.id}&period=2026-10`, { headers: f.headers }),
  );
  expect(stillTrial.reports.find((row: { id: string }) => row.id === report.report.id).mode).toBe(
    'trial',
  );
});
