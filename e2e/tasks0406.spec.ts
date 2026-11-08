import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function fixture(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const company = await (
    await request.post('/api/companies', {
      headers,
      data: { name: '0406虚构企业' + crypto.randomUUID(), tax_id: '' },
    })
  ).json();
  const book = await (
    await request.post('/api/books', {
      headers,
      data: { name: '0406虚构账套', company_id: company.id, period: '2026-10' },
    })
  ).json();
  return { company, book, headers };
}
async function screenshot(page: Page, label: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  mkdirSync('tmp_test/tasks0406-20261107/screenshots', { recursive: true });
  await page.screenshot({
    path: `tmp_test/tasks0406-20261107/screenshots/${label}.png`,
    fullPage: true,
  });
}

for (const width of [1365, 390]) {
  test(`0406 ${width} 邀请明确发送且未知不重发`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { company } = await fixture(request);
    const record = {
      id: 'synthetic-invite',
      email: 'invited@example.test',
      name: '虚构员工',
      status: 'awaiting_activation',
      company_id: company.id,
      company_role: 'employee',
      attempts: 1,
    };
    const activation =
      'https://example.test/account-activation#request_id=synthetic-invite&token=synthetic-secret&mode=existing';
    let sends = 0;
    await page.route(/\/api\/admin\/account-requests(?:\/|\?|$)/, (route) => {
      const url = route.request().url();
      if (url.endsWith('/invitation-delivery'))
        return route.fulfill({
          json: {
            request_id: record.id,
            request_attempt: 1,
            email: record.email,
            status: record.status,
            enabled: true,
            delivery: null,
          },
        });
      if (url.endsWith('/send-invitation')) {
        sends++;
        expect(route.request().postDataJSON().activation_url).toBe(activation);
        return route.fulfill({ json: { id: 'send-one', state: 'unknown' } });
      }
      if (route.request().method() === 'GET')
        return route.fulfill({ json: { requests: [], provisioning_enabled: true } });
      return route.fulfill({ status: 201, json: { request: record, activation_url: activation } });
    });
    await page.goto(`/admin/organizations/${company.id}?tab=members`);
    await page.getByRole('button', { name: '开通账号', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '开通账号', exact: true });
    await dialog.getByRole('radio', { name: /本人激活/ }).check();
    await dialog.getByLabel('姓名', { exact: true }).fill(record.name);
    await dialog.getByLabel('登录邮箱', { exact: true }).fill(record.email);
    await dialog.getByRole('button', { name: '生成激活链接', exact: true }).click();
    await expect(dialog.getByText(`收件邮箱：${record.email}`, { exact: false })).toBeVisible();
    expect(sends).toBe(0);
    await dialog.getByRole('button', { name: '发送邀请邮件', exact: true }).click();
    await expect(dialog.getByRole('status')).toContainText('发送结果未知');
    await expect(dialog.getByRole('button', { name: '发送邀请邮件', exact: true })).toBeDisabled();
    expect(sends).toBe(1);
    expect(
      await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
    ).not.toContain('synthetic-secret');
    await screenshot(page, `invite-${width}`);
  });
  test(`0406 ${width} 通道回执与本人签收分开并拒绝错版本`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { company, book } = await fixture(request);
    const item = {
      id: 'synthetic-notice',
      kind: 'signature_reminder',
      state: 'unknown',
      semantic: 'unknown',
      attempts: 1,
      recipient: { user_id: 'recipient', name: '虚构客户' },
      package: { id: 'pack', version: 2, manifest_sha256: 'a'.repeat(64) },
      channel: 'smtp',
      can_investigate: false,
      created_at: '',
      updated_at: '',
      dispatched_at: '',
      opened_at: '',
      acknowledged_at: '',
      last_error: '',
    };
    let bad = false,
      denied = false,
      writes = 0;
    await page.route('**/api/books/*/delivery/notifications**', (route) => {
      if (route.request().method() === 'POST') writes++;
      if (denied) return route.fulfill({ status: 403, json: { detail: '授权已撤销' } });
      if (route.request().url().endsWith('/receipts'))
        return route.fulfill({
          json: {
            notification_id: item.id,
            book_id: book.id,
            company_id: company.id,
            package_id: 'pack',
            package_version: bad ? 3 : 2,
            manifest_sha256: 'a'.repeat(64),
            attempts: 1,
            recipient_user_id: 'recipient',
            accepted: true,
            delivered: true,
            viewed: true,
            bounced: true,
            signed: false,
            customer_opened: false,
            receipt_conflict: true,
            can_retry: false,
          },
        });
      return route.fulfill({ json: { book_id: book.id, period: book.period, items: [item] } });
    });
    await page.goto(
      `/workspace/delivery?book=${book.id}&company=${company.id}&period=${book.period}`,
    );
    await page.getByRole('button', { name: '查看送达证据', exact: true }).click();
    const panel = page.getByRole('region', { name: '通知送达证据' });
    await expect(panel).toContainText('客户本人签收：尚未签收');
    await expect(panel).toContainText('客户登录后打开：暂无记录');
    await expect(panel.getByRole('alert')).toContainText('送达与退信证据冲突');
    await screenshot(page, `receipts-${width}`);
    bad = true;
    await panel.getByRole('button', { name: '重新读取送达证据' }).click();
    await expect(panel.getByRole('alert')).toContainText('回执范围、版本或收件人已变化');
    await expect(panel.getByText('客户本人签收：尚未签收')).toHaveCount(0);
    denied = true;
    await panel.getByRole('button', { name: '重新读取送达证据' }).click();
    await expect(page.getByRole('region', { name: '当前登录身份' })).toContainText(
      '权限待重新确认',
    );
    await expect(page.getByRole('region', { name: '通知送达证据' })).toHaveCount(0);
    await expect(page.getByText('客户本人签收：尚未签收')).toHaveCount(0);
    expect(writes).toBe(0);
  });
  test(`0406 ${width} 单人政策须确认且历史分类保持`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const { company, book, headers } = await fixture(request);
    await page.goto(`/admin/organizations/${company.id}?tab=books`);
    await page.getByLabel('分工账套').selectOption(book.id);
    const panel = page.getByRole('region', { name: '账套复核政策' });
    await expect(panel).toContainText('暂无已审样本');
    await panel.getByLabel('办理方式').selectOption('single_operator');
    await panel.getByLabel('变更原因').fill('本地虚构单人政策，安排后续抽查');
    await expect(panel.getByRole('button', { name: '保存复核政策' })).toBeDisabled();
    await panel.getByRole('checkbox').check();
    await panel.getByRole('button', { name: '保存复核政策' }).click();
    await expect(panel).toContainText('政策版本 1');
    await expect(panel.getByRole('region', { name: '本账套复核分类' })).toContainText(
      '独立复核 0 项；单人办理 0 项',
    );
    const saved = await (
      await request.get(`/api/books/${book.id}/review-policy`, { headers })
    ).json();
    expect(saved.policy.mode).toBe('single_operator');
    expect(saved.review_summary.total_reviewed).toBe(0);
    await screenshot(page, `policy-${width}`);
  });
}
