import { expect, test } from '@playwright/test';
import { choosePersona, openNavigation } from './ui-navigation';

test.setTimeout(60_000);
for (const width of [1440, 390]) {
  test(`岗位授权真实保存与撤销 ${width}`, async ({ page, request }) => {
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const employeeSession = await (await request.get('/api/session?persona=employee')).json();
    const employeeMe = await (
      await request.get('/api/me', {
        headers: { Authorization: `Bearer ${employeeSession.token}` },
      })
    ).json();
    const colleague = { user_id: employeeMe.user.id, email: employeeMe.user.email };
    const grant = await request.put('/api/firm/members', {
      headers,
      data: { email: colleague.email, role: 'viewer' },
    });
    expect(grant.ok()).toBe(true);
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/workspace/firm');
    await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
    await openNavigation(page);
    const settings = page
      .getByRole('navigation', { name: '主导航', exact: true })
      .locator('details[aria-label="设置"]');
    await settings.locator('summary').click();
    await settings.getByRole('button', { name: '成员与账套授权', exact: true }).click();
    const panel = page.getByRole('region', { name: '岗位范围与期限' });
    await expect(panel).toBeVisible();
    const member = panel.getByLabel('岗位成员');
    await expect.poll(() => member.locator('option').count()).toBeGreaterThan(1);
    await member.selectOption({ index: 1 });
    await panel.getByLabel('岗位模板', { exact: true }).selectOption('auditor');
    await panel.getByLabel('岗位授权理由').fill('虚构浏览器验收限定范围');
    await expect(panel.getByRole('button', { name: '保存岗位授权' })).toBeDisabled();
    await panel.getByLabel('岗位客户范围').selectOption({ index: 2 });
    const response = page.waitForResponse(
      (r) => r.url().endsWith('/api/firm/access-assignments') && r.request().method() === 'POST',
    );
    await panel.getByRole('button', { name: '保存岗位授权' }).click();
    expect((await response).status()).toBe(201);
    await expect(panel.getByRole('button', { name: '撤销此授权' })).toHaveCount(1);
    await panel.getByRole('button', { name: '撤销此授权' }).click();
    await expect(panel.getByRole('button', { name: '撤销此授权' })).toHaveCount(0);
    await expect(panel.getByText(/已撤销/).first()).toBeVisible();
    await page.screenshot({ path: `tmp_test/access-policy-admin-${width}.png`, fullPage: true });
    expect((await request.delete(`/api/firm/members/${colleague.user_id}`, { headers })).ok()).toBe(
      true,
    );
  });

  test(`员工签收按准确版本接纳及拒绝错范围 ${width}`, async ({ page, request }) => {
    const employeeSession = await (await request.get('/api/session?persona=employee')).json();
    const employeeMe = await (
      await request.get('/api/me', {
        headers: { Authorization: `Bearer ${employeeSession.token}` },
      })
    ).json();
    const employee = { user_id: employeeMe.user.id };
    await page.setViewportSize({ width, height: 900 });
    const ref = {
      id: 'synthetic-package',
      book_id: 'synthetic-book',
      company_id: 'synthetic-company',
      company_name: '虚构企业',
      period: '2026-10',
      version: 2,
      manifest_sha256: 'a'.repeat(64),
    };
    await page.route('**/api/my-delivery-packages', (route) =>
      route.fulfill({ json: { packages: [ref] } }),
    );
    let wrong = false;
    let acknowledgements = 0;
    await page.route(
      '**/api/books/synthetic-book/delivery/monthly-package/synthetic-package/*',
      async (route) => {
        const action = new URL(route.request().url()).pathname.split('/').at(-1);
        if (action === 'acknowledge') acknowledgements++;
        const value = {
          ...ref,
          company_id: wrong ? 'another-company' : ref.company_id,
          report_id: 'report-1',
          report_version: 1,
          status: 'frozen',
          created_at: '2026-10-31',
          created_by: 'accountant',
          contact_user_id: employee.user_id,
          superseded: false,
          can_open: true,
          can_acknowledge: true,
          customer_opening_status: 'opened',
          opening: { opened_by: employee.user_id, opened_at: '2026-10-31' },
          customer_receipt_status: 'not_recorded',
          acknowledgement: null,
          external_import_status: 'separate_workflow',
          files: [{ path: 'README.html', size_bytes: 5, sha256: 'b'.repeat(64) }],
          file_count: 1,
        };
        await route.fulfill({ json: value });
      },
    );
    await page.goto('/workspace/home');
    await choosePersona(page, 'employee');
    await page.getByText('我需要签收的资料包', { exact: true }).click();
    await page.getByRole('button', { name: '查看这版资料' }).click();
    await expect(page.getByText('README.html', { exact: true })).toBeVisible();
    expect(acknowledgements).toBe(0);
    await page.screenshot({
      path: `tmp_test/access-policy-recipient-${width}.png`,
      fullPage: true,
    });
    wrong = true;
    await page.getByRole('button', { name: '查看这版资料' }).click();
    await expect(page.getByText(/资料包版本校验失败/)).toBeVisible();
    await expect(page.getByText('README.html', { exact: true })).toHaveCount(0);
    expect(acknowledgements).toBe(0);
  });

  test(`部门费用月份切换和坏金额阻断 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let invalid = false;
    await page.route('**/api/companies/*/departments/*/expenses?*', async (route) => {
      const url = new URL(route.request().url());
      const parts = url.pathname.split('/');
      await route.fulfill({
        json: {
          company_id: parts[3],
          department_id: parts[5],
          period: url.searchParams.get('period'),
          expense_cents: invalid ? '1234' : 1234,
        },
      });
    });
    await page.goto('/workspace/home');
    await choosePersona(page, 'department');
    await page.getByText('本部门费用', { exact: true }).click();
    const department = page.getByLabel('费用查询部门');
    await expect.poll(() => department.locator('option').count()).toBeGreaterThan(1);
    await department.selectOption({ index: 1 });
    await expect(page.getByText(/已入账费用净额 ¥12.34/)).toBeVisible();
    await page.screenshot({
      path: `tmp_test/access-policy-department-${width}.png`,
      fullPage: true,
    });
    invalid = true;
    await page.getByLabel('部门费用月份').fill('2026-09');
    await expect(page.getByText(/费用汇总范围或金额不完整/)).toBeVisible();
    await expect(page.getByText(/已入账费用净额/)).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}
