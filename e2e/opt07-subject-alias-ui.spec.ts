import { expect, test } from '@playwright/test';

test('OPT07-F 客商别名可在账簿界面登记、解析展示并停用', async ({ page, request }) => {
  test.setTimeout(120_000);
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const created = await request.post('/api/books', {
    headers,
    data: { name: `OPT07 别名界面 ${crypto.randomUUID().slice(0, 8)}`, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const entity = await request.post(`/api/books/${book.id}/ledger/entities`, {
    headers,
    data: { kind: 'counterparty', code: 'ALIAS-UI-1', name: '虚构客商界面' },
  });
  expect(entity.status()).toBe(201);

  await page.goto(`/workspace/ledger?book=${book.id}`, { waitUntil: 'domcontentloaded' });
  const panel = page.getByRole('region', { name: '客商通用别名' });
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await expect(panel.getByText('当前企业尚未登记客商别名。')).toBeVisible();

  await panel.getByLabel('对应客商主数据').selectOption({ label: '虚构客商界面 · ALIAS-UI-1' });
  await panel.getByLabel('历史名称 / 简称').fill('客商旧称-UI');
  await panel.getByLabel('来源', { exact: true }).fill('历史台账');
  await panel.getByRole('button', { name: '登记别名', exact: true }).click();
  await expect(panel.getByText('客商别名已登记；解析使用生效期内的唯一匹配。')).toBeVisible();
  await expect(panel.getByText('客商旧称-UI', { exact: true })).toBeVisible();
  await expect(panel.getByText('→ 虚构客商界面', { exact: true })).toBeVisible();

  await panel.getByLabel(/停用原因/).fill('客户名称变更');
  await panel.getByRole('button', { name: '停用别名', exact: true }).click();
  await expect(panel.getByText('别名已停用；历史凭证仍保留原名称。')).toBeVisible();
  await expect(panel.getByText('已停用 · 2026-10-01 起', { exact: true })).toBeVisible();

  await page.screenshot({ path: 'artifacts/opt07-subject-alias-1366.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: 'artifacts/opt07-subject-alias-390.png', fullPage: true });
});
