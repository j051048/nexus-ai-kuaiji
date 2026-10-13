import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('合成兼容HTTP端点贯通真实模型网关、后台轮次、费用及人工确认任务', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` }, tag = crypto.randomUUID().slice(0, 8);
  const book = await (await request.post('/api/books', { headers, data: { name: `合成模型企业 ${tag}`, period: '2026-10' } })).json();
  expect((await request.put(`/api/companies/${book.company_id}/intelligence/settings`, { headers, data: { enabled: true } })).status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/imports`, { headers, data: { source_name: '合成模型流水', rows: [
    { external_id: tag, date: '2026-10-05', description: `合成用品 ${tag}`, counterparty: '合成供应商', amount: '55.01', direction: 'expense', evidence_ref: '合成回单' },
  ] } })).status()).toBe(200);
  await page.goto('/'); await choosePersona(page, 'accountant');
  await page.getByRole('button', { name: '问会计助手', exact: true }).click();
  const assistant = page.getByRole('dialog', { name: '会计助手', exact: true });
  await assistant.getByLabel('助手所属企业', { exact: true }).selectOption(book.company_id);
  await assistant.locator('label').filter({ hasText: `合成模型企业 ${tag}` }).getByRole('checkbox').check();
  await assistant.getByRole('button', { name: /创建会话/ }).click();
  await assistant.getByLabel('向会计助手提问', { exact: true }).fill('合成验收：请解释范围并准备流水草稿预览');
  await assistant.getByRole('button', { name: '发送会计助手问题', exact: true }).click();
  await expect(assistant.locator('.ga-message.assistant')).toContainText('合成模型验收回复');
  await expect(assistant.locator('.ga-citations')).toContainText(`合成模型企业 ${tag}`);
  expect((await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts).toHaveLength(0);
  await assistant.locator('.ga-task-proposal').click();
  const plan = assistant.locator('.ga-action-plan');
  await expect(plan).toContainText(`合成用品 ${tag}`);
  await plan.getByRole('button', { name: '确认执行这 1 项', exact: true }).click();
  await expect(plan.locator('.ga-receipt')).toContainText('1 份草稿已生成');
  const overview = await (await request.get(`/api/operations/companies/${book.company_id}/overview`, { headers })).json();
  expect(overview.costs).toHaveLength(1); expect(overview.costs[0].kind).toBe('chat');
  expect(overview.costs[0].estimated_cost).toBe('0.000080');
  const persisted = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(persisted.drafts[0].status).toBe('draft');
  await page.screenshot({ path: 'artifacts/stage15-assistant-model-desktop.png', fullPage: false });
  await assistant.getByRole('button', { name: '关闭全局会计助手', exact: true }).click();
  await page.getByRole('button', { name: '问会计助手', exact: true }).click();
  await assistant.getByLabel('助手所属企业', { exact: true }).selectOption(book.company_id);
  await assistant.getByRole('button', { name: '查看我的会话', exact: true }).click();
  await assistant.locator('.ga-history-row').first().click();
  await expect(assistant.locator('.ga-message.assistant')).toContainText('合成模型验收回复');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(assistant).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage15-assistant-model-mobile.png', fullPage: false });
  await page.keyboard.press('Escape'); await expect(assistant).toHaveCount(0);
});
