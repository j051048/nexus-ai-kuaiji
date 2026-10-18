import { expect, test, type Page } from '@playwright/test';

test.setTimeout(60_000);

const company = { id: 'firm-access-company', name: '访问回归验证企业', status: 'active' };
const pages = [
  { route: 'ingestion', button: '数据接入中心', heading: '集成与数据接入中心' },
  { route: 'observability', button: '运行可观测性', heading: '运行可观测性' },
  { route: 'onboarding', button: '客户开通', heading: '客户标准化开通' },
] as const;

async function mockFirmPages(page: Page) {
  const denied = new Set<string>();
  let adminReads = 0;
  let identityReads = 0;
  let legacyWorkerResponse = false;
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/me' || path === '/api/workspace/bootstrap') identityReads += 1;
  });
  await page.route('**/api/admin/companies**', (route) => {
    adminReads += 1;
    return route.fulfill({ status: 403, json: { detail: '管理接口需要二次验证' } });
  });
  await page.route('**/api/companies', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { companies: [company] } })
      : route.continue(),
  );
  const forbidden = { detail: '当前角色无权访问此资源' };
  await page.route('**/api/firm/ingestion', (route) =>
    denied.has('ingestion')
      ? route.fulfill({ status: 403, json: forbidden })
      : route.fulfill({
          json: {
            connections: [
              {
                id: 'fixture-connection',
                company_id: company.id,
                kind: 'webhook',
                name: '验证专用连接',
                status: 'active',
                last_cursor: 'cursor-001',
                created_at: '2026-10-18T00:00:00Z',
              },
            ],
            runs: [],
          },
        }),
  );
  await page.route('**/api/firm/observability?**', (route) =>
    denied.has('observability')
      ? route.fulfill({ status: 403, json: forbidden })
      : route.fulfill({
          json: {
            queues: { ocr: {} },
            alerts: {},
            metrics: {
              buckets: 1,
              transactions: 123456,
              writes: 2,
              rollbacks: 0,
              avg_connection_ms: 1,
              avg_lock_ms: 0,
              max_transaction_ms: 2,
            },
            ai_cost_cny: 0,
            projection: { total: 1, dirty: 0, never_built: 0, failing: 0, oldest_dirty_at: '' },
            trace_hint: '仅显示当前事务所指标',
            ...(legacyWorkerResponse
              ? {}
              : {
                  worker_health: {
                    available: true,
                    last_heartbeat_at: '2026-10-18T00:00:00Z',
                    stale_after_seconds: 90,
                  },
                }),
            // Rolling deployments may still include the old global field; it must not be rendered.
            workers: [
              {
                worker_id: 'global-worker-private-id',
                state: 'running',
                heartbeat_at: '2026-10-18T00:00:00Z',
                inflight_count: 4,
              },
            ],
          },
        }),
  );
  await page.route('**/api/firm/onboarding/preview?**', (route) =>
    denied.has('onboarding')
      ? route.fulfill({ status: 403, json: forbidden })
      : route.fulfill({
          json: {
            company_id: company.id,
            period: new URL(route.request().url()).searchParams.get('period'),
            ready: true,
            missing_steps: [],
            blockers: [],
            target_book_id: 'fixture-book',
            source_book_id: null,
            checklist: {
              steps: [
                {
                  id: 'fixture-check',
                  title: '原企业开通检查结果',
                  complete: true,
                  required: true,
                  detail: '仅供本地回归验证',
                },
              ],
              ready: true,
              note: '本地回归检查结果',
            },
          },
        }),
  );
  return {
    denied,
    adminReads: () => adminReads,
    identityReads: () => identityReads,
    useLegacyWorkers: () => {
      legacyWorkerResponse = true;
    },
  };
}

async function openPage(page: Page, item: (typeof pages)[number]) {
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: item.button, exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`/workspace/${item.route}`));
  await expect(page.getByRole('heading', { name: item.heading, exact: true })).toBeVisible();
}

async function readyHome(page: Page) {
  await page.goto('/workspace/home');
  await expect(page.getByRole('button', { name: '刷新工作台', exact: true })).toBeEnabled({
    timeout: 30_000,
  });
}

test('三个事务所页面正常加载，不访问管理企业接口，不重建登录身份', async ({ page }) => {
  const control = await mockFirmPages(page);
  await readyHome(page);
  const identityReads = control.identityReads();
  for (const item of pages) {
    await openPage(page, item);
    if (item.route === 'ingestion')
      await expect(page.getByText('验证专用连接', { exact: true })).toBeVisible();
    if (item.route === 'observability') {
      await expect(page.getByText('后台处理服务', { exact: true })).toBeVisible();
      await expect(page.getByText('可用', { exact: true })).toBeVisible();
      await expect(page.getByText('global-worker-private-id')).toHaveCount(0);
    }
    if (item.route === 'onboarding')
      await expect(page.getByText('原企业开通检查结果', { exact: true })).toBeVisible();
  }
  expect(control.adminReads()).toBe(0);
  expect(control.identityReads()).toBe(identityReads);
});

for (const item of pages) {
  test(`${item.button}收到普通资源403后保留原页面并清除旧结果`, async ({ page }) => {
    const control = await mockFirmPages(page);
    await readyHome(page);
    await openPage(page, item);
    const content =
      item.route === 'ingestion'
        ? '验证专用连接'
        : item.route === 'observability'
          ? '123456'
          : '原企业开通检查结果';
    await expect(page.getByText(content, { exact: true })).toBeVisible();
    const identityReads = control.identityReads();
    control.denied.add(item.route);
    if (item.route === 'onboarding') {
      await expect(page.getByLabel('开通月份')).toBeEnabled();
      await page.getByLabel('开通月份').fill('2027-02');
    } else {
      await page
        .locator(`.firm-${item.route}`)
        .getByRole('button', { name: '刷新', exact: true })
        .click();
    }
    await expect(page.getByRole('alert')).toContainText('当前角色无权访问此资源');
    await expect(page.getByText(content, { exact: true })).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`/workspace/${item.route}`));
    await expect(page.getByRole('heading', { name: item.heading, exact: true })).toBeVisible();
    await expect.poll(control.identityReads).toBe(identityReads + 1);
    await expect(page).toHaveURL(new RegExp(`/workspace/${item.route}`));
    await expect(page.getByRole('heading', { name: item.heading, exact: true })).toBeVisible();
  });
}

test('滚动部署缺少worker_health时显示未知，仍不展示旧全局worker内容', async ({ page }) => {
  const control = await mockFirmPages(page);
  control.useLegacyWorkers();
  await readyHome(page);
  await openPage(page, pages[1]);
  await expect(page.getByText('状态未知', { exact: true })).toBeVisible();
  await expect(page.getByText('global-worker-private-id')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '运行可观测性', exact: true })).toBeVisible();
});

test('真正401会清除登录与页面数据，不后台自动重新登录', async ({ page }) => {
  const control = await mockFirmPages(page);
  await readyHome(page);
  await openPage(page, pages[1]);
  await expect(page.getByText('123456', { exact: true })).toBeVisible();
  const reads = control.identityReads();
  await page.route('**/api/firm/observability?**', (route) =>
    route.fulfill({
      status: 401,
      json: { detail: '登录已过期' },
    }),
  );
  await page
    .locator('.firm-observability')
    .getByRole('button', { name: '刷新', exact: true })
    .click();
  await expect(page.locator('.auth-shell')).toBeVisible();
  await expect(page.getByText('登录已失效，请重新登录。', { exact: true })).toBeVisible();
  await expect(page.locator('.firm-observability')).toHaveCount(0);
  expect(control.identityReads()).toBe(reads);
});
