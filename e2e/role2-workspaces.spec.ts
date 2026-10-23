import { expect, test } from '@playwright/test';
import { choosePersona, openNavigation } from './ui-navigation';

test.setTimeout(60_000);

test('员工主任务：先提交与看进度，授权说明折叠，复用原录入页面', async ({ page }) => {
  const summaryRequests: string[] = [];
  page.on('request', (req) => {
    if (new URL(req.url()).pathname === '/api/agent-workspace/summary')
      summaryRequests.push(req.url());
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  const primary = page.getByRole('region', { name: '提交与进度', exact: true });
  await expect(primary).toBeVisible({ timeout: 4000 });
  const info = page.getByRole('region', { name: '当前职责与授权范围' }).locator('details');
  await expect(info).not.toHaveAttribute('open', '');
  await page.screenshot({ path: 'tmp_test/role2-employee-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.screenshot({ path: 'tmp_test/role2-employee-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await primary.getByRole('button', { name: /提交报销/ }).click();
  await expect(page.getByRole('dialog').getByLabel('事项金额')).toBeVisible();
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toHaveCount(0);
  expect(summaryRequests).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('企业财务主任务：核验移交和资料入口使用现有真实权限页面', async ({ page }) => {
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'finance');
  const primary = page.getByRole('region', { name: '资料核验与移交', exact: true });
  await expect(primary).toBeVisible({ timeout: 4000 });
  await page.screenshot({ path: 'tmp_test/role2-finance-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'tmp_test/role2-finance-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await primary.getByRole('button', { name: '查看待核验事项', exact: true }).click();
  await expect(page.getByRole('tab', { name: '企业财务', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page
    .getByRole('navigation', { name: '客户服务' })
    .getByRole('button', { name: '待核验', exact: true })
    .click();
  await primary.getByRole('button', { name: '查看资料清单', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/materials/);
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0);
});

test('事务所工作台：客户进度、人员管理和开通复用已有模块', async ({ page }) => {
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible({
    timeout: 4000,
  });
  await expect(page.getByRole('region', { name: '全所运营概览' })).toBeVisible();
  await page.screenshot({ path: 'tmp_test/role2-firm-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'tmp_test/role2-firm-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: '人员管理', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/members/);
  await expect(page.getByRole('heading', { name: '事务所人员', exact: true })).toBeVisible();
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '客户进度', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/monthly/);
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '新增客户', exact: true }).click();
  await expect(page.getByRole('heading', { name: '客户标准化开通', exact: true })).toBeVisible();
});

test('技术入口集中设置，深链接保留唯一高亮，手机能打开并关闭设置', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await openNavigation(page);
  const settings = page
    .locator('details')
    .filter({ has: page.locator('summary').filter({ hasText: /^设置$/ }) });
  await expect(settings).toHaveCount(1, { timeout: 4000 });
  await expect(settings).not.toHaveAttribute('open', '');
  await settings.locator('summary').click();
  await settings.getByRole('button', { name: '运行可观测性', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/observability/);
  await expect(page.getByRole('heading', { name: '运行可观测性', exact: true })).toBeVisible();
  await openNavigation(page);
  await expect(page.locator('.agent-sidebar [aria-current="page"]')).toHaveCount(1);
  await page.getByRole('button', { name: '关闭导航', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('客户财务专业入口按需展开，不展示事务所技术设置', async ({ page }) => {
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'finance');
  await expect(page.getByRole('navigation', { name: '设置入口' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '运行可观测性', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
});

test('企业财务的直接全所链接不查询全所数据，真实服务端同样拒绝', async ({ page, request }) => {
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${owner.token}` };
  const financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const context = await (
    await request.get('/api/business/context', { headers: financeHeaders })
  ).json();
  const created = await request.post('/api/books', {
    headers,
    data: {
      name: `虚构权限隔离-${crypto.randomUUID()}`,
      company_id: context.companies[0].id,
      period: '2026-10',
    },
  });
  expect(created.ok()).toBe(true);
  const book = await created.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers,
        data: { role: 'viewer' },
      })
    ).status(),
  ).toBe(200);
  try {
    await page.route('**/api/session', async (route) => {
      const url = new URL(route.request().url());
      url.searchParams.set('persona', 'finance');
      await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
    });
    const reads: string[] = [];
    page.on('request', (req) => {
      if (new URL(req.url()).pathname === '/api/firm/cockpit') reads.push(req.url());
    });
    await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('全所工作台由事务所负责人管理', { exact: true })).toBeVisible();
    expect(reads).toEqual([]);
    expect(
      (await request.get('/api/firm/cockpit?period=2026-10', { headers: financeHeaders })).status(),
    ).toBe(403);
  } finally {
    await request.delete(`/api/books/${book.id}/members/local-business-finance`, { headers });
  }
});

test('管理员展示身份有全所能力时默认落到管理工作台，管理后台仍可由设置打开', async ({ page }) => {
  // Presentation projection only; API grants remain the real local owner's.
  await page.route(/\/api\/workspace\/(bootstrap|profile)(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const value = body.profile ?? body;
    const profile = { ...value, display_role: 'admin', can_firm_operations: true };
    await route.fulfill({ response, json: body.profile ? { ...body, profile } : profile });
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/workspace\/firm/);
  await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
  const settings = page.locator('details[aria-label="设置"]');
  await settings.locator('summary').click();
  await settings.getByRole('button', { name: '企业与人员管理', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
});
