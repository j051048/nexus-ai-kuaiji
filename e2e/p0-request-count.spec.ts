import { expect, test } from '@playwright/test';

test.setTimeout(90_000);

test('P0 首页 bootstrap 单次、客户账套按需读取', async ({ page }) => {
  const reads: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'GET' && request.url().includes('/api/'))
      reads.push(new URL(request.url()).pathname);
  });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await expect(page.getByLabel('切换验证身份')).toHaveValue('accountant');
  await expect(page.getByLabel('我的工作概览')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('正在读取我的工作范围…', { exact: true })).toHaveCount(0);
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
  expect(reads).not.toContain('/api/workbench');
  expect(reads.some((path) => /^\/api\/books\/[^/]+/.test(path))).toBe(false);

  const nav = page.getByRole('navigation', { name: '主导航' });
  const accounting = nav.getByRole('button', { name: '客户企业', exact: true });
  await expect(accounting).toBeVisible();
  await accounting.click();
  await expect.poll(() => reads.filter((path) => path === '/api/workbench').length).toBe(1);
  await expect(page.getByLabel('搜索企业名称').first()).toBeVisible({ timeout: 30_000 });
  expect(reads.filter((path) => path === '/api/workspace/bootstrap')).toHaveLength(1);
});
