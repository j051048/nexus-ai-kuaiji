import { openAccounting } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('企业管理员明确启用条件续跑、保留版本依据，耗时按企业展示', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json(), headers = { Authorization: `Bearer ${session.token}` };
  const name = `合成续跑配置 ${crypto.randomUUID().slice(0, 8)}`;
  const created = await request.post('/api/books', { headers, data: { name, period: '2026-10' } }); expect(created.status()).toBe(201); const book = await created.json();
  await page.goto('/'); await openAccounting(page); await page.getByLabel('搜索企业名称').fill(name); await page.getByRole('listitem').filter({ hasText: name }).click();
  const panel = page.getByLabel('后台续跑与耗时'); await expect(panel).toContainText('默认关闭');
  await panel.getByRole('button', { name: '续跑配置与耗时', exact: true }).click();
  await panel.getByLabel('允许本企业条件自动续跑', { exact: true }).check();
  await panel.getByLabel('自动续跑配置依据', { exact: true }).fill('试点企业仅核验资料后自动恢复');
  await panel.getByRole('button', { name: '保存续跑配置', exact: true }).click();
  await expect(panel).toContainText('已启用受控续跑');
  const saved = await (await request.get(`/api/intelligence/companies/${book.company_id}/automation-policy`, { headers })).json(); expect(saved.enabled).toBe(true); expect(saved.version).toBe(1);
  await expect(panel).toContainText('本企业最近 7 日后台耗时');
  await panel.getByLabel('允许本企业条件自动续跑', { exact: true }).uncheck();
  await panel.getByLabel('自动续跑配置依据', { exact: true }).fill('暂停试点自动恢复安排');
  await panel.getByRole('button', { name: '保存续跑配置', exact: true }).click();
  await expect(panel).toContainText('已关闭自动续跑');
  await page.screenshot({ path: 'artifacts/stage11-queue-automation.png', fullPage: true });
});
