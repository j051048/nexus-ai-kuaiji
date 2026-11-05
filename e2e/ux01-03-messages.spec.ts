import { test, expect, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

const message = {
  id: 'audit-message',
  company_id: 'synthetic-company',
  kind: 'return',
  subject: '虚构退回：请补原件',
  body: '仅本地界面审计夹具，不是真实企业资料',
  related_kind: 'business_event',
  related_ref: 'synthetic-event',
  created_at: '2026-11-03T00:00:00Z',
  read_at: null,
};
async function openMessages(page: Page) {
  await page.goto('/workspace/home');
  await choosePersona(page, 'employee');
  await page
    .getByRole('navigation', { name: '客户服务', exact: true })
    .getByRole('button', { name: '消息', exact: true })
    .click();
  await expect(page.getByText(message.subject, { exact: true })).toBeVisible();
}
test.setTimeout(60_000);
for (const width of [1280, 390]) {
  test(`消息刷新被撤权后应清除旧内容 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    let revoked = false;
    await page.route('**/api/client/messages?*', async (route) => {
      await route.fulfill({
        status: revoked ? 403 : 200,
        json: revoked
          ? { detail: '审计故障注入：消息读取已撤权' }
          : { messages: [message], unread: 1 },
      });
    });
    await openMessages(page);
    revoked = true;
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: '审计故障注入：消息读取已撤权' }),
    ).toContainText('消息读取已撤权');
    await page.screenshot({
      path: `tmp_test/ux01-03-20261103/messages-revoked-${width}.png`,
      fullPage: true,
    });
    await expect(page.getByText(message.subject, { exact: true })).toHaveCount(0);
  });
  test(`消息标已读失败可恢复且不重发 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    let writes = 0;
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/client/messages?*', (route) =>
      route.fulfill({ json: { messages: [message], unread: 1 } }),
    );
    await page.route('**/api/client/messages/audit-message/read', (route) => {
      writes++;
      return route.fulfill({ status: 503, json: { detail: '本地审计临时不可用' } });
    });
    await openMessages(page);
    await Promise.all([
      page.waitForResponse('**/api/client/messages/audit-message/read'),
      page.getByRole('button', { name: '标为已读', exact: true }).click(),
    ]);
    await page.screenshot({
      path: `tmp_test/ux01-03-20261103/messages-read-error-${width}.png`,
      fullPage: true,
    });
    await test
      .info()
      .attach('page-errors', { body: JSON.stringify(errors), contentType: 'application/json' });
    await expect.soft(page.getByRole('alert')).toBeVisible();
    expect(errors).toEqual([]);
    await expect(page.getByRole('button', { name: '标为已读', exact: true })).toBeDisabled();
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(page.getByRole('button', { name: '标为已读', exact: true })).toBeEnabled();
    expect(writes).toBe(1);
  });
}
