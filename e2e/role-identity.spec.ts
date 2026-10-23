import { expect, test, type Page, type Response } from '@playwright/test';
import type { WorkspaceProfile } from '../src/workspace-types';

test.setTimeout(90_000);

// These tests use an isolated seeded database. Projection overrides below only
// exercise UI identity contracts; they are not authorization evidence.
async function selectIdentity(page: Page, persona: string) {
  const change = page.getByLabel('切换验证身份');
  await expect(change).toBeVisible({ timeout: 30_000 });
  if ((await change.inputValue()) !== persona) {
    const profiles: Response[] = [];
    const observe = (response: Response) => {
      if (
        ['/api/workspace/bootstrap', '/api/workspace/profile'].includes(
          new URL(response.url()).pathname,
        )
      )
        profiles.push(response);
    };
    page.on('response', observe);
    try {
      const [response] = await Promise.all([
        page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === '/api/session' &&
            new URL(response.url()).searchParams.get('persona') === persona,
        ),
        change.selectOption(persona),
      ]);
      const { token } = await response.json();
      await expect
        .poll(
          () =>
            profiles.some(
              (response) =>
                response.request().headers().authorization === `Bearer ${token}` && response.ok(),
            ),
          { timeout: 30_000 },
        )
        .toBe(true);
    } finally {
      page.off('response', observe);
    }
  }
  await expect(change).toHaveValue(persona);
}

async function expectIdentity(page: Page, title: string, surface: 'client' | 'accounting') {
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByRole('region', { name: '当前职责与授权范围', exact: true })).toContainText(
    title,
  );
  await expect(
    page.locator(surface === 'client' ? '.client-portal' : '.agent-sidebar'),
  ).toBeVisible();
}

async function bindReloadIdentity(page: Page, persona: string) {
  // Local demo deliberately starts a fresh owner session on reload. Route that
  // fixture session to the same real seeded identity; all grants/API reads are
  // still evaluated by the actual local server. This is not Supabase login QA.
  await page.unroute('**/api/session');
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    const response = await route.fetch({ url: url.toString() });
    await route.fulfill({ response });
  });
}

async function projectIdentity(page: Page, patch: Partial<WorkspaceProfile>) {
  await page.route(/\/api\/workspace\/(?:bootstrap|profile)(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const profile = body.profile ?? body;
    const projected = { ...profile, ...patch };
    await route.fulfill({
      response,
      json: body.profile ? { ...body, profile: projected } : projected,
    });
  });
}

test('岗位身份：真实员工、部门审批人与企业财务的标题、身份说明和导航一致', async ({ page }) => {
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  // Finish initial authentication before switching; professional deep links
  // below instead wait for their real workbench content to finish loading.
  await expect(page.getByRole('region', { name: '当前职责与授权范围', exact: true })).toBeVisible({
    timeout: 30_000,
  });
  for (const [persona, title, home, business] of [
    ['employee', '我的工作台', '首页', '我的事项'],
    ['department', '部门审批', '待审批', '部门事项'],
    ['finance', '财务核验', '待核验', '业务事项'],
  ]) {
    await selectIdentity(page, persona);
    await expectIdentity(page, title, 'client');
    const nav = page.getByRole('navigation', { name: '客户服务', exact: true });
    await expect(nav.getByRole('button', { name: home, exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(nav.getByRole('button', { name: business, exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
    await bindReloadIdentity(page, persona);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expectIdentity(page, title, 'client');
  }
});

test('岗位身份：真实财务兼有账套复核权保持客户门户，显式专业入口和撤权后首页均重新核权', async ({
  page,
  request,
}) => {
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${owner.token}` };
  const financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const context = await (
    await request.get('/api/business/context', { headers: financeHeaders })
  ).json();
  const company = context.companies[0];
  const response = await request.post('/api/books', {
    headers,
    data: {
      name: `岗位呈现虚构账套-${crypto.randomUUID().slice(0, 8)}`,
      company_id: company.id,
      period: '2026-10',
    },
  });
  expect(response.ok()).toBe(true);
  const book = await response.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  try {
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('region', { name: '当前职责与授权范围', exact: true })).toBeVisible(
      { timeout: 30_000 },
    );
    await selectIdentity(page, 'finance');
    const profile = await (
      await request.get('/api/workspace/profile', { headers: financeHeaders })
    ).json();
    expect(profile.can_accounting).toBe(true);
    expect(profile.can_review).toBe(true);
    expect(profile.display_role).toBe('finance');
    await expectIdentity(page, '财务核验', 'client');
    await page.locator('.client-portal-settings > summary').click();
    await page.getByRole('button', { name: '会计工作区', exact: true }).click();
    await expect(page).toHaveURL(/\/workspace\/accounting$/);
    await expect(page.locator('.agent-sidebar')).toBeVisible();
    await expect(page.getByLabel('搜索企业名称').first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole('link', { name: '衡账，返回工作台', exact: true }).click();
    await expectIdentity(page, '财务核验', 'client');
    await expect(page.getByLabel('搜索企业名称')).toHaveCount(0);
    expect(
      (
        await request.delete(`/api/books/${book.id}/members/local-business-finance`, { headers })
      ).status(),
    ).toBe(200);
    await bindReloadIdentity(page, 'finance');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expectIdentity(page, '财务核验', 'client');
    expect((await request.get(`/api/books/${book.id}`, { headers: financeHeaders })).status()).toBe(
      404,
    );
  } finally {
    await request.delete(`/api/books/${book.id}/members/local-business-finance`, { headers });
  }
});

for (const [displayRole, title, surface] of [
  ['manager', '企业工作台', 'client'],
  ['accountant', '会计作业', 'accounting'],
  ['reviewer', '会计复核', 'accounting'],
  ['firm_owner', '事务所管理', 'accounting'],
  ['admin', '系统管理', 'accounting'],
] as const) {
  test(`岗位呈现投影：${displayRole} 显式首页和刷新保留一致身份`, async ({ page }) => {
    await projectIdentity(page, {
      // The old compatibility role intentionally differs. Primary presentation
      // must come from the server's display_role, not additive capabilities.
      role: displayRole === 'manager' ? 'employee' : 'accountant',
      display_role: displayRole,
      can_submit: true,
      can_manage:
        displayRole === 'manager' || displayRole === 'firm_owner' || displayRole === 'admin',
      can_accounting: displayRole !== 'manager',
      can_review:
        displayRole === 'reviewer' || displayRole === 'firm_owner' || displayRole === 'admin',
      can_firm_operations: displayRole === 'firm_owner' || displayRole === 'admin',
      can_finance: false,
      can_approve: false,
    });
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expectIdentity(page, title, surface);
    await expect(page).toHaveURL(/\/workspace\/home$/);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expectIdentity(page, title, surface);
    await expect(page).toHaveURL(/\/workspace\/home$/);
    if (displayRole === 'manager') {
      const nav = page.getByRole('navigation', { name: '客户服务', exact: true });
      await expect(nav.getByRole('button', { name: '企业首页', exact: true })).toHaveAttribute(
        'aria-current',
        'page',
      );
      await expect(nav.getByRole('button', { name: '报告', exact: true })).toBeVisible();
      await expect(nav.getByRole('button', { name: '我的事项', exact: true })).toHaveCount(0);
    }
  });
}

test('岗位兼任复核：财务门户同屏复核可打开并返回，未审凭证不可导出', async ({ page, request }) => {
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${owner.token}` };
  const financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const context = await (
    await request.get('/api/business/context', { headers: financeHeaders })
  ).json();
  const company = context.companies[0];
  const tag = crypto.randomUUID().slice(0, 8);
  const title = `财务门户同屏办公用品-${tag}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name: `财务门户复核虚构账套-${tag}`, company_id: company.id, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  try {
    expect(
      (
        await request.post(`/api/books/${book.id}/imports`, {
          headers,
          data: {
            source_name: '虚构财务门户复核流水，仅本地规则测试',
            rows: [
              {
                external_id: `role-inline-${tag}`,
                date: '2026-10-05',
                description: title,
                counterparty: '虚构文具商',
                amount: '100.00',
                direction: 'expense',
                evidence_ref: '合成文字来源，不调用模型，不进行审核',
              },
            ],
          },
        })
      ).status(),
    ).toBe(200);
    // Existing deterministic suggestion endpoint; no model/provider is configured.
    expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(
      200,
    );
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('region', { name: '当前职责与授权范围', exact: true })).toBeVisible(
      { timeout: 30_000 },
    );
    await selectIdentity(page, 'finance');
    await expectIdentity(page, '财务核验', 'client');
    await page.getByLabel('筛选待办类型', { exact: true }).selectOption('review');
    const task = page.locator('.workspace-task').filter({ hasText: title });
    await expect(task).toBeVisible({ timeout: 30_000 });
    await task.getByRole('button', { name: '同屏复核', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect(drawer.getByText('当前来源只有文字引用', { exact: true })).toBeVisible();
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
    await expect(drawer).toHaveCount(0);
    await expectIdentity(page, '财务核验', 'client');
    await expect(page).toHaveURL(/\/workspace\/home$/);
    await expect(task.getByRole('button', { name: '同屏复核', exact: true })).toBeEnabled();
    const exports = await (await request.get(`/api/books/${book.id}/exports`, { headers })).json();
    expect(exports.exports).toEqual([]);
  } finally {
    await request.delete(`/api/books/${book.id}/members/local-business-finance`, { headers });
  }
});

test('岗位身份切换：离开专业账套后不保留旧企业详情和专业导航', async ({ page }) => {
  await page.goto('/workspace/accounting', { waitUntil: 'domcontentloaded' });
  await expect(page.getByLabel('搜索企业名称').first()).toBeVisible({ timeout: 30_000 });
  await selectIdentity(page, 'employee');
  await expectIdentity(page, '我的工作台', 'client');
  await expect(page).toHaveURL(/\/workspace\/home$/);
  await expect(page.getByLabel('搜索企业名称')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '客户服务', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '导入流水', exact: true })).toHaveCount(0);
  await bindReloadIdentity(page, 'employee');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expectIdentity(page, '我的工作台', 'client');
});
