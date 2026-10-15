import { expect, test } from '@playwright/test';
import { choosePersona, openAccounting, openManagement, openNavigation } from './ui-navigation';

test.setTimeout(90_000);

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => (resolve = done));
  return { promise, resolve };
}

function observeReads(page: import('@playwright/test').Page) {
  const paths: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'GET' && request.url().includes('/api/'))
      paths.push(new URL(request.url()).pathname);
  });
  return paths;
}

async function readyHome(page: import('@playwright/test').Page) {
  await expect(page.getByLabel('我的工作概览')).toBeVisible();
  await expect(page.getByText('正在读取我的工作范围…', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新工作台', exact: true })).toBeEnabled();
}

test('首页只请求待办所需数据，会计工作区按需读取，运营权限入口不依赖完整企业列表', async ({
  page,
}) => {
  const reads = observeReads(page);
  await page.goto('/workspace/home');
  await readyHome(page);
  await expect(page.locator('summary').filter({ hasText: /^会计工作区$/ })).toBeVisible();
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
  expect(reads).not.toContain('/api/me');
  expect(reads).not.toContain('/api/workspace/profile');
  expect(reads).not.toContain('/api/workspace/tasks');
  expect(reads).toContain('/api/agent-workspace/summary');
  expect(reads).not.toContain('/api/workbench');
  expect(reads).not.toContain('/api/operations/companies');
  expect(reads).not.toContain('/api/business/context');
  expect(reads.some((path) => /^\/api\/books\/[^/]+/.test(path))).toBe(false);

  await openAccounting(page);
  await expect.poll(() => reads.filter((path) => path === '/api/workbench').length).toBe(1);
  await expect(
    page.getByRole('list', { name: '客户企业', exact: true }).getByRole('listitem').first(),
  ).toBeVisible();
  await expect.poll(() => reads.some((path) => /^\/api\/books\/[^/]+$/.test(path))).toBe(true);
  expect(reads).not.toContain('/api/business/context');

  await openManagement(page);
  await page.getByRole('button', { name: '费用与运行监控', exact: true }).click();
  await expect
    .poll(() => reads.filter((path) => path === '/api/operations/companies').length)
    .toBe(1);
  await expect(page.getByLabel('监控客户企业')).toBeVisible();
});

test('离开会计工作区取消慢读取；回到首页不再启动凭证瀑布，重新进入时重新核权', async ({ page }) => {
  const reads = observeReads(page);
  const arrived = deferred(),
    released = deferred();
  let held = false;
  await page.route('**/api/workbench?**', async (route) => {
    if (held) return route.continue();
    held = true;
    const response = await route.fetch();
    arrived.resolve();
    await released.promise;
    await route.fulfill({ response }).catch(() => {});
  });
  await page.goto('/workspace/home');
  await readyHome(page);
  expect(reads).not.toContain('/api/workspace/tasks');
  const summary = page.locator('summary').filter({ hasText: /^会计工作区$/ });
  await summary.click();
  await page
    .getByRole('navigation', { name: '会计协作', exact: true })
    .getByRole('button', { name: '会计工作台', exact: true })
    .click();
  await arrived.promise;
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '我的待办', exact: true })
    .click();
  released.resolve();
  await readyHome(page);
  await expect.poll(() => reads.filter((path) => path === '/api/workspace/tasks').length).toBe(1);
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
  expect(reads.filter((path) => path === '/api/workbench').length).toBe(1);
  expect(reads.some((path) => /^\/api\/books\/[^/]+/.test(path))).toBe(false);
  await openAccounting(page);
  await expect.poll(() => reads.filter((path) => path === '/api/workbench').length).toBe(2);
  await expect(
    page.getByRole('list', { name: '客户企业', exact: true }).getByRole('listitem').first(),
  ).toBeVisible();
});

test('切换为员工后不保留会计和运行监控入口，不恢复旧账套或隐藏预取', async ({ page }) => {
  const reads = observeReads(page);
  await page.goto('/workspace/home');
  await readyHome(page);
  await openAccounting(page);
  await expect(
    page.getByRole('list', { name: '客户企业', exact: true }).getByRole('listitem').first(),
  ).toBeVisible();
  await choosePersona(page, 'employee');
  await readyHome(page);
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
  expect(reads.filter((path) => path === '/api/me')).toHaveLength(1);
  await expect(page.locator('summary').filter({ hasText: /^会计工作区$/ })).toHaveCount(0);
  await openManagement(page);
  await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
  const after = reads.length;
  await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
  await readyHome(page);
  expect(
    reads
      .slice(after)
      .some(
        (path) =>
          path === '/api/workbench' ||
          path === '/api/operations/companies' ||
          /^\/api\/books\/[^/]+/.test(path),
      ),
  ).toBe(false);
});

test('首页关键待办不等待运行摘要，摘要失败可独立重试且不重复读取身份和待办', async ({ page }) => {
  const reads = observeReads(page);
  const arrived = deferred(),
    released = deferred();
  let first = true;
  await page.route('**/api/agent-workspace/summary', async (route) => {
    if (!first) return route.continue();
    first = false;
    arrived.resolve();
    await released.promise;
    await route
      .fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"detail":"合成运行摘要暂不可用"}',
      })
      .catch(() => {});
  });
  try {
    await page.goto('/workspace/home');
    await arrived.promise;
    await readyHome(page);
    await expect(page.getByText('正在读取待办…', { exact: true })).toHaveCount(0);
    await expect(
      page
        .locator('.workspace-summary')
        .getByRole('button', { name: /需要我处理/ })
        .locator('strong'),
    ).not.toHaveText('—');
    await expect(page.getByRole('button', { name: '筛选待办企业', exact: true })).toBeEnabled();
    await expect(page.getByText('读取真实运行状态', { exact: false })).toBeVisible();
    expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
    expect(reads).not.toContain('/api/workspace/tasks');
    released.resolve();
    await page.getByRole('button', { name: '重试运行概览', exact: true }).click();
    await expect(page.getByRole('button', { name: '重试运行概览', exact: true })).toHaveCount(0);
    await expect(page.getByText('读取真实运行状态', { exact: false })).toHaveCount(0);
    expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
    expect(reads).not.toContain('/api/me');
    expect(reads).not.toContain('/api/workspace/tasks');
  } finally {
    released.resolve();
  }
});

test('初始待办只消费一次，筛选、手动刷新及回到首页都实时读取授权范围', async ({ page }) => {
  const reads = observeReads(page);
  await page.goto('/workspace/home');
  await readyHome(page);
  expect(reads).not.toContain('/api/workspace/tasks');
  await page.getByLabel('筛选待办类型').selectOption('review');
  await expect.poll(() => reads.filter((path) => path === '/api/workspace/tasks').length).toBe(1);
  await readyHome(page);
  await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
  await expect.poll(() => reads.filter((path) => path === '/api/workspace/tasks').length).toBe(2);
  await readyHome(page);
  expect(reads.filter((path) => path === '/api/workspace/profile')).toHaveLength(1);
  await openAccounting(page);
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '我的待办', exact: true })
    .click();
  await expect.poll(() => reads.filter((path) => path === '/api/workspace/tasks').length).toBe(3);
  await readyHome(page);
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
});

test('会计深链接直接读取身份和当前工作区，不预取首页待办', async ({ page }) => {
  const reads = observeReads(page);
  await page.goto('/workspace/accounting');
  await expect(page.getByRole('list', { name: '客户企业', exact: true })).toBeVisible();
  expect(reads.filter((path) => path === '/api/me')).toHaveLength(1);
  expect(reads).not.toContain('/api/workspace/bootstrap');
  expect(reads).not.toContain('/api/workspace/tasks');
  expect(reads).not.toContain('/api/agent-workspace/summary');
});

test('滚动部署时仅缺少bootstrap路由才兼容旧接口', async ({ page }) => {
  const reads = observeReads(page);
  await page.route('**/api/workspace/bootstrap', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not Found"}' }),
  );
  await page.goto('/workspace/home');
  await readyHome(page);
  await expect(page.getByText('正在读取待办…', { exact: true })).toHaveCount(0);
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
  expect(reads.filter((path) => path === '/api/me')).toHaveLength(1);
  expect(reads).toContain('/api/workspace/profile');
  expect(reads).toContain('/api/workspace/tasks');
});

test('bootstrap拒绝授权时不回退旧身份，也不读取待办或运行摘要', async ({ page }) => {
  const reads = observeReads(page);
  await page.route('**/api/workspace/bootstrap', (route) =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: '{"detail":"当前事务所授权已撤销"}',
    }),
  );
  await page.goto('/workspace/home');
  await expect(page.getByRole('alert')).toContainText('当前事务所授权已撤销');
  expect(reads).not.toContain('/api/me');
  expect(reads).not.toContain('/api/workspace/profile');
  expect(reads).not.toContain('/api/workspace/tasks');
  expect(reads).not.toContain('/api/agent-workspace/summary');
  await expect(page.getByLabel('我的工作概览')).toHaveCount(0);
});

test('退出后晚回的bootstrap不能恢复旧身份，重新选择员工只保留新scope', async ({ page }) => {
  const arrived = deferred(),
    released = deferred();
  let held = false;
  await page.route('**/api/workspace/bootstrap', async (route) => {
    if (held) return route.continue();
    held = true;
    const response = await route.fetch();
    arrived.resolve();
    await released.promise;
    await route.fulfill({ response }).catch(() => {});
  });
  try {
    await page.goto('/e2e/fixtures/auth-scope-harness.html');
    await arrived.promise;
    await page.getByRole('button', { name: '退出', exact: true }).click();
    released.resolve();
    await expect(page.getByRole('button', { name: '使用员工身份', exact: true })).toBeEnabled();
    await expect(page.getByTestId('identity')).toHaveText('未登录');
    await expect(page.getByTestId('seed')).toHaveText('没有首页数据');
    await page.getByRole('button', { name: '使用员工身份', exact: true }).click();
    await expect(page.getByTestId('identity')).toHaveText('示例员工 小陈');
    await expect(page.getByTestId('seed')).toHaveText('已有首页数据');
  } finally {
    released.resolve();
  }
});
