import { expect, test } from '@playwright/test';

test.setTimeout(90_000);

test('同屏复核保留包选择，修改后重新准备，手机原件详情与返回不丢进度', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` }, tag = crypto.randomUUID().slice(0, 8);
  const party = `同屏供应商 ${tag}`, name = `第十二阶段同屏企业 ${tag}`;
  let response = await request.post('/api/books', { headers, data: { name, period: '2026-10' } });
  expect(response.status()).toBe(201); const book = await response.json();
  expect((await request.post(`/api/books/${book.id}/ledger/entities`, { headers, data: { kind: 'counterparty', code: `INLINE-${tag}`, name: party } })).status()).toBe(201);
  response = await request.post(`/api/books/${book.id}/imports`, { headers, data: { source_name: '虚构同屏复核流水', rows: [
    { external_id: `${tag}-1`, date: '2026-10-05', description: `同屏办公用品甲 ${tag}`, counterparty: party, amount: '100.00', direction: 'expense', evidence_ref: '合成归档索引甲，仅验证流程' },
    { external_id: `${tag}-2`, date: '2026-10-06', description: `同屏办公用品乙 ${tag}`, counterparty: party, amount: '125.00', direction: 'expense', evidence_ref: '合成归档索引乙，仅验证流程' },
  ] } }); expect(response.status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  await page.goto('/', { waitUntil: 'domcontentloaded' }); await page.getByLabel('切换验证身份').selectOption('accountant');
  await page.getByRole('navigation', { name: '主导航' }).getByRole('button', { name: '跨企业批量复核', exact: true }).click();
  await page.getByLabel('复核企业', { exact: true }).selectOption(book.company_id);
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '准备同类复核包', exact: true }).click();
  await expect(page.locator('.review-packet-item')).toHaveCount(2);
  await page.getByLabel('选择复核包内当前检查通过的凭证', { exact: true }).check();
  const first = page.locator('.review-packet-item').filter({ hasText: `同屏办公用品甲 ${tag}` });
  await first.getByRole('button', { name: '打开原件与凭证', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText('当前来源只有文字引用', { exact: true })).toBeVisible();
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await drawer.getByLabel('同屏凭证摘要', { exact: true }).fill(`会计核对办公用品甲 ${tag}`);
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer.getByText('有尚未保存的修改', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: '继续修改', exact: true }).click();
  await drawer.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(drawer.getByRole('status')).toContainText('草稿已保存');
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByText('凭证已更新，原复核预览已暂停', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '生成所选复核预览', exact: true })).toBeDisabled();
  await expect(page.getByLabel(`选择复核包凭证 同屏办公用品乙 ${tag}`, { exact: true })).toBeChecked();
  await page.getByRole('button', { name: '按当前版本重新准备', exact: true }).click();
  await expect(page.getByText('凭证已更新，原复核预览已暂停', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel(`选择复核包凭证 会计核对办公用品甲 ${tag}`, { exact: true })).toBeChecked();
  await expect(page.getByLabel(`选择复核包凭证 同屏办公用品乙 ${tag}`, { exact: true })).toBeChecked();
  await page.getByRole('button', { name: '生成所选复核预览', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认所选复核包', exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.review-packet-item').first().getByRole('button', { name: '打开原件与凭证', exact: true }).click();
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('button', { name: '返回列表', exact: true })).toBeVisible();
  await expect(drawer.locator('.inline-review-facts')).toBeVisible();
  await expect(drawer.getByText('当前来源只有文字引用', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage12-inline-review-mobile.png', fullPage: false });
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认所选复核包', exact: true })).toBeDisabled();
  await page.getByLabel('确认已核对同类复核包的证据和差异', { exact: true }).check();
  await page.getByRole('button', { name: '确认所选复核包', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已审核 2 张');
  expect((await (await request.get(`/api/books/${book.id}/exports`, { headers })).json()).exports).toEqual([]);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  const denied = await request.get(`/api/books/${book.id}?focus_draft_id=${detail.drafts[0].id}&limit=1`, { headers: { Authorization: `Bearer ${employee.token}` } });
  expect([403, 404]).toContain(denied.status());
});
