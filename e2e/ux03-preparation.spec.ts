import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
for (const viewport of [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`填写日期时即知交接准备，不改业务日期 ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home');
    await choosePersona(page, 'employee');
    await page
      .getByRole('region', { name: '提交与进度' })
      .getByRole('button', { name: /提交报销/ })
      .click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('发生日期', { exact: true }).fill('2035-01-03');
    const preparation = dialog.getByRole('region', { name: '交接准备', exact: true });
    await expect(preparation).toContainText('2035-01 尚未开账');
    await expect(preparation).toContainText('请勿为通过检查改动真实发生日期');
    await expect(dialog.getByLabel('发生日期', { exact: true })).toHaveValue('2035-01-03');
    await dialog.getByRole('checkbox').check();
    await expect(dialog.getByRole('button', { name: '暂存草稿', exact: true })).toBeEnabled();
    await preparation.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `tmp_test/ux01-03-20261103/preparation-${viewport.width}.png`,
      fullPage: true,
    });
    await dialog.getByLabel('发生日期', { exact: true }).fill('2026-10-03');
    await expect(preparation).toContainText('2026-10 已准备接收账务');
    await expect(preparation).not.toContainText('2035-01');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}

test('准备检查拒绝错企业响应，切换日期后不接纳迟到建议', async ({ page }) => {
  await page.goto('/workspace/home');
  await choosePersona(page, 'employee');
  await page
    .getByRole('region', { name: '提交与进度' })
    .getByRole('button', { name: /提交报销/ })
    .click();
  const dialog = page.getByRole('dialog');
  const preparation = dialog.getByRole('region', { name: '交接准备', exact: true });
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = false;
  await page.route('**/api/business/preparation?*', async (route) => {
    const parameters = new URL(route.request().url()).searchParams;
    if (parameters.get('occurred_on') === '2035-02-03') {
      started = true;
      await waiting;
      await route.fulfill({
        json: {
          company_id: parameters.get('company_id'),
          period: '2035-02',
          status: 'ready',
          owner_role: 'finance',
          next_action: '旧月份迟到建议',
          advisory: true,
        },
      });
    } else {
      await route.fulfill({
        json: {
          company_id: 'wrong-company',
          period: '2035-03',
          status: 'ready',
          owner_role: 'finance',
          next_action: '其他企业的建议绝不能显示',
          advisory: true,
        },
      });
    }
  });
  try {
    await dialog.getByLabel('发生日期', { exact: true }).fill('2035-02-03');
    await expect.poll(() => started).toBe(true);
    await dialog.getByLabel('发生日期', { exact: true }).fill('2035-03-03');
    await expect(preparation).toContainText('与当前企业或月份不符');
    release();
    await expect(preparation).not.toContainText('旧月份迟到建议');
    await expect(preparation).not.toContainText('其他企业的建议绝不能显示');
  } finally {
    release();
  }
});

for (const width of [1280, 390]) {
  test(`财务核验之前看见未开账原因 ${width}`, async ({ page, request }) => {
    const headers = async (persona: string) => ({
      Authorization: `Bearer ${(await (await request.get(`/api/session?persona=${persona}`)).json()).token}`,
    });
    const employee = await headers('employee');
    const department = await headers('department');
    const context = await (
      await request.get('/api/business/context', { headers: employee })
    ).json();
    const company = context.companies.find((item: { can_submit: boolean }) => item.can_submit);
    const response = await request.post('/api/business/events', {
      headers: employee,
      data: {
        company_id: company.id,
        department_id: company.department_id,
        template_id: 'reimbursement',
        payload: {
          occurred_on: '2035-01-03',
          description: '未开账前置核验',
          counterparty: '虚构文具商',
          amount: '12.30',
          direction: 'expense',
          payment_status: 'unpaid',
          category: 'office',
        },
      },
    });
    expect(response.status()).toBe(201);
    let event = await response.json();
    const upload = await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers: employee,
        multipart: {
          file: {
            name: '虚构票据.png',
            mimeType: 'image/png',
            buffer: Buffer.from(
              'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
              'base64',
            ),
          },
        },
      },
    );
    expect(upload.ok()).toBe(true);
    event = await upload.json();
    const submit = await request.post(`/api/business/events/${event.id}/submit`, {
      headers: employee,
      data: { expected_version: event.version },
    });
    expect(submit.ok()).toBe(true);
    event = await submit.json();
    const approval = await request.post(`/api/business/events/${event.id}/department-approve`, {
      headers: department,
      data: { expected_version: event.version },
    });
    expect(approval.ok()).toBe(true);
    await page.setViewportSize({ width, height: 844 });
    await page.route('**/api/session', async (route) =>
      route.fulfill({
        response: await route.fetch({ url: route.request().url() + '?persona=finance' }),
      }),
    );
    await page.goto(`/workspace/business?event=${event.id}`);
    const preparation = page.getByRole('region', { name: '交接准备', exact: true });
    await expect(preparation).toContainText('2035-01 尚未开账');
    await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toBeVisible();
    await preparation.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `tmp_test/ux01-03-20261103/finance-preparation-${width}.png`,
      fullPage: true,
    });
    const current = await (
      await request.get(`/api/business/events/${event.id}`, { headers: await headers('finance') })
    ).json();
    expect(current.status).toBe('department_approved');
    expect(current.mapping).toBeNull();
  });
}
