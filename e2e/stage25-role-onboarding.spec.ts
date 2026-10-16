import { expect, test } from '@playwright/test';
import { choosePersona, openNavigation } from './ui-navigation';

test('员工首页聚焦提交与资料，移除技术摘要请求，手机导航可到本人资料清单', async ({ page }) => {
  const summaries: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/agent-workspace/summary')) summaries.push(request.url());
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await choosePersona(page, 'employee');
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  const before = summaries.length;
  await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  await expect(page.getByText('Agent 后台', { exact: true })).toHaveCount(0);
  await openNavigation(page);
  await expect(page.getByRole('button', { name: '后台运行', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '票据处理任务', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '资料清单', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/materials/);
  await expect(page.getByRole('heading', { name: '需要我提供的资料', exact: true })).toBeVisible();
  expect(summaries.length).toBe(before);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage25-employee-materials-mobile.png', fullPage: true });
});

test('企业开通清单按当前月份读取，返回配置页面后可继续检查', async ({ page, request }) => {
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${owner.token}` };
  const response = await request.post('/api/companies', {
    headers, data: { name: `开通清单虚构企业-${crypto.randomUUID().slice(0, 8)}`, tax_id: '' },
  });
  expect(response.status()).toBe(201);
  const company = await response.json();
  await page.goto(`/admin/organizations/${company.id}?tab=setup`);
  await expect(page.getByRole('heading', { name: '把这家企业接入日常工作', exact: true })).toBeVisible();
  await page.getByLabel('开通检查月份', { exact: true }).fill('2026-10');
  await expect(page.getByLabel('企业开通清单')).toContainText('本月 0 个账套');
  const step = page.getByLabel('企业开通清单').getByRole('listitem').filter({ hasText: '选择资料提交方式' });
  await step.getByRole('button', { name: '去完成', exact: true }).click();
  await expect(page).toHaveURL(/\?tab=departments$/);
  await page.getByRole('tab', { name: '开通清单', exact: true }).click();
  await expect(page.getByRole('heading', { name: '把这家企业接入日常工作', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('企业开通清单')).toContainText('验证一笔提交至审核后导出');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage25-onboarding-mobile.png', fullPage: true });
});
