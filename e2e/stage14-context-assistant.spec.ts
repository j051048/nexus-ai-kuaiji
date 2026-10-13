import { expect, test } from '@playwright/test';
import { openNavigation } from './ui-navigation';

test('会计上下文助手只解释当前保存资料，换凭证清空问答，手机保持人工审核入口', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` }, tag = crypto.randomUUID().slice(0, 8), party = `合成助手供应商 ${tag}`;
  const created = await request.post('/api/books', { headers, data: { name: `上下文合成企业 ${tag}`, period: '2026-10' } });
  expect(created.status()).toBe(201); const book = await created.json();
  expect((await request.post(`/api/books/${book.id}/ledger/entities`, { headers, data: { kind: 'counterparty', code: `CTX-${tag}`, name: party } })).status()).toBe(201);
  expect((await request.post(`/api/books/${book.id}/imports`, { headers, data: { source_name: '合成上下文验收流水', rows: [
    { external_id: `${tag}-1`, date: '2026-10-05', description: `合成办公用品甲 ${tag}`, counterparty: party, amount: '100.01', direction: 'expense', evidence_ref: `合成归档索引甲 ${tag}` },
    { external_id: `${tag}-2`, date: '2026-10-06', description: `合成办公用品乙 ${tag}`, counterparty: party, amount: '125.02', direction: 'expense', evidence_ref: `合成归档索引乙 ${tag}` },
  ] } })).status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  await page.goto('/'); await page.getByLabel('切换验证身份').selectOption('accountant');
  await openNavigation(page);
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '跨企业批量复核', exact: true }).click();
  await page.getByLabel('复核企业', { exact: true }).selectOption(book.company_id);
  await page.locator('.batch-review-row').filter({ hasText: `合成办公用品甲 ${tag}` }).getByRole('button', { name: '查看证据', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: '会计助手', exact: true }).click();
  const assistant = drawer.getByRole('complementary', { name: '当前凭证会计助手', exact: true });
  await expect(assistant.getByRole('heading', { name: '依据当前资料', exact: true })).toBeVisible();
  await expect(assistant.locator('.context-scope-card')).toContainText('凭证 v1');
  await assistant.getByRole('button', { name: '金额与借贷是否一致？', exact: true }).click();
  await expect(assistant.locator('.context-answer-card')).toContainText('100.01 元');
  await expect(assistant.locator('.context-answer-card')).toContainText('借贷相等');
  await assistant.getByLabel('询问当前凭证', { exact: true }).fill('这家公司老板今年的个人生日是哪天？');
  await assistant.getByRole('button', { name: '发送当前凭证问题', exact: true }).click();
  await expect(assistant.locator('.context-answer-card').last()).toContainText('当前材料不足以回答');
  await assistant.locator('.context-basis-card').getByRole('button', { name: '当前来源记录', exact: true }).click();
  await expect(drawer.getByText('当前来源只有文字引用', { exact: true })).toBeVisible();
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  // At tablet and phone widths the same assistant becomes a separate sheet.
  await expect(assistant).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage14-context-assistant-mobile.png', fullPage: false });
  await page.keyboard.press('Escape');
  await expect(assistant).toHaveCount(0);
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeVisible();
  const next = drawer.getByRole('button', { name: '下一笔', exact: true });
  if (await next.isEnabled()) await next.click(); else await drawer.getByRole('button', { name: '上一笔', exact: true }).click();
  await drawer.getByRole('button', { name: '会计助手', exact: true }).click();
  await expect(assistant.locator('.context-answer-turn')).toHaveCount(0);
  await assistant.getByRole('button', { name: '金额与借贷是否一致？', exact: true }).click();
  await expect(assistant.locator('.context-answer-card')).toContainText('125.02 元');
  await expect(assistant).not.toContainText('100.01 元');
  await page.keyboard.press('Escape');
  await expect(drawer).toBeVisible();
  const persisted = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(persisted.drafts).toHaveLength(2);
  expect(persisted.drafts.every((draft: { status: string; version: number }) => draft.status === 'draft' && draft.version === 1)).toBe(true);
  expect((await (await request.get(`/api/books/${book.id}/exports`, { headers })).json()).exports).toEqual([]);
});
