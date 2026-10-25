import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${device} V07 岗位说明复用折叠位置，兼任角色切换不带入旧岗位指引`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home');
    await expect(
      page.getByRole('region', { name: '当前职责与授权范围', exact: true }),
    ).toBeVisible();
    await choosePersona(page, 'employee');
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    const region = page.getByRole('region', { name: '当前职责与授权范围', exact: true });
    const instructions = region.locator('[aria-label="当前岗位入门说明"]');
    await expect(instructions).not.toBeVisible();
    const writes: string[] = [];
    page.on('request', (req) => {
      if (
        new URL(req.url()).pathname.startsWith('/api/') &&
        !['GET', 'HEAD'].includes(req.method())
      )
        writes.push(req.url());
    });
    await region.locator('summary').focus();
    await page.keyboard.press('Enter');
    await expect(instructions).toContainText('上传原件');
    await expect(instructions).toContainText('提交不等于已经记账');
    await expect(instructions).not.toContainText('分录草稿');
    const firstDefinition = instructions.locator('dd').first();
    expect(await firstDefinition.evaluate((node) => getComputedStyle(node).textAlign)).toBe('left');
    const titleBox = await instructions.locator('dt').first().boundingBox();
    const definitionBox = await firstDefinition.boundingBox();
    expect(definitionBox!.y).toBeGreaterThanOrEqual(titleBox!.y + titleBox!.height);
    await instructions.screenshot({ path: `tmp_test/v07/green/employee-help-${device}.png` });
    await choosePersona(page, 'finance');
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    await expect(instructions).not.toBeVisible();
    await region.locator('summary').click();
    await expect(instructions).toContainText('核对原件');
    await expect(instructions).toContainText('会计仍须独立复核');
    await expect(instructions).not.toContainText('员工垫付款项');
    expect(writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await instructions.screenshot({ path: `tmp_test/v07/green/finance-help-${device}.png` });
  });
}

test('V07 事务所负责人复用原使用说明，先安排客户责任与期限', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('/workspace/firm');
  const help = page.getByRole('button', { name: '使用说明与版本范围', exact: true });
  await expect(help).toBeVisible();
  const writes: string[] = [];
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(req.method()))
      writes.push(req.url());
  });
  await help.click();
  const dialog = page.getByRole('dialog', { name: '从可靠的第一笔账开始', exact: true });
  const instructions = dialog.locator('[aria-label="当前岗位入门说明"]');
  await expect(instructions).toContainText('客户月度进度');
  await expect(instructions).toContainText('资料负责人');
  await expect(instructions).not.toContainText('选择报销');
  await dialog.screenshot({ path: 'tmp_test/v07/green/firm-help-desktop.png' });
  await dialog.getByRole('button', { name: '开始处理', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('V07 阅读会计术语时快捷键不得保存、审核或切换凭证', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const name = `V07虚构快捷键-${crypto.randomUUID()}`;
  const created = await request.post('/api/books', { headers, data: { name, period: '2026-10' } });
  expect(created.status()).toBe(201);
  const book = await created.json();
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '虚构办公流水，仅本地快捷键验收',
          rows: [
            {
              external_id: name,
              date: '2026-10-12',
              description: name,
              counterparty: '虚构文具商',
              amount: '80.00',
              direction: 'expense',
              evidence_ref: '仅合成文字依据，无真实原件',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  await page.goto('/workspace/home');
  await page.getByLabel('筛选待办类型', { exact: true }).selectOption('review');
  const row = page.locator('.workspace-task').filter({ hasText: name });
  await expect(row).toBeVisible();
  await row.getByRole('button').click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toHaveValue(name);
  const writes: string[] = [];
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(req.method()))
      writes.push(req.url());
  });
  await drawer.getByLabel('同屏凭证摘要', { exact: true }).fill(`${name}未保存修改`);
  const save = drawer.getByRole('button', { name: '保存草稿', exact: true });
  await expect(save).toBeEnabled();
  const summary = drawer.locator('summary').filter({ hasText: '借方和贷方是什么？' });
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(drawer.getByText(/不能直接当成收入和支出/)).toBeVisible();
  await page.keyboard.press('Control+s');
  await page.keyboard.press('j');
  await page.keyboard.press('a');
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect(writes).toEqual([]);
  await expect(save).toBeEnabled();
  await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toHaveValue(`${name}未保存修改`);
  await drawer.screenshot({ path: 'tmp_test/v07/green/accounting-help-desktop.png' });
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer.getByText('有尚未保存的修改', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: '放弃修改并返回列表', exact: true }).click();
  expect(writes).toEqual([]);
});
