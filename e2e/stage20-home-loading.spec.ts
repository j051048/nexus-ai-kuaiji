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
  expect(reads).toContain('/api/workspace/profile');
  expect(reads).toContain('/api/workspace/tasks');
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
