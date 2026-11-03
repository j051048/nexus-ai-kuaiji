import { expect, test } from '@playwright/test';
import { openNavigation, openManagement } from './ui-navigation';

test('会计Agent工作区支持精确金额分诊、保存视图、企业快搜与上下文命令入口', async ({
  page,
  request,
}) => {
  const session = await (await request.get('/api/session?persona=accountant')).json(),
    headers = { Authorization: `Bearer ${session.token}` };
  const tag = crypto.randomUUID().slice(0, 8),
    name = `合成Agent分诊 ${tag}`;
  const response = await request.post('/api/books', { headers, data: { name, period: '2026-10' } });
  expect(response.status()).toBe(201);
  const book = await response.json();
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '合成分诊流水',
          rows: [
            {
              external_id: `${tag}-1`,
              date: '2026-10-05',
              description: `办公采购 ${tag}`,
              counterparty: '合成供应商',
              amount: '100.01',
              direction: 'expense',
              evidence_ref: '合成回单索引',
            },
            {
              external_id: `${tag}-2`,
              date: '2026-10-06',
              description: `项目服务 ${tag}`,
              counterparty: '合成客户',
              amount: '125.02',
              direction: 'income',
              evidence_ref: '合成收款索引',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/workspace/home');
  await expect(page.getByRole('heading', { name: '事务所管理', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '紧凑表格视图', exact: true }).click();
  await page.getByLabel('待办排序', { exact: true }).selectOption('amount');
  await page.getByRole('button', { name: '筛选待办企业', exact: true }).click();
  await page
    .getByRole('region', { name: '企业快搜', exact: true })
    .getByLabel('搜索企业名称', { exact: true })
    .fill(tag);
  await page
    .getByRole('region', { name: '企业快搜', exact: true })
    .getByRole('button')
    .filter({ hasText: name })
    .click();
  await expect(page.locator('.workspace-task')).toHaveCount(2);
  await expect(page.locator('.workspace-task').first()).toContainText('125.02');
  await page.screenshot({ path: 'artifacts/stage14-agent-workspace-desktop.png', fullPage: false });
  await page.getByRole('button', { name: '保存视图', exact: true }).click();
  await expect(page.locator('.workspace-saved-views')).toContainText('视图');
  await page.reload();
  await expect(page.getByRole('heading', { name: '事务所管理', exact: true })).toBeVisible();
  await page
    .getByLabel('常用工作视图', { exact: true })
    .getByRole('button', { name: /1 · 待处理 · 指定企业/ })
    .click();
  await expect(page.getByLabel('待办排序', { exact: true })).toHaveValue('amount');
  await expect(page.locator('.workspace-task')).toHaveCount(2);
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog', { name: '快速进入工作', exact: true });
  await expect(palette).toBeVisible();
  await palette.getByLabel('查找企业或工作入口', { exact: true }).fill(tag);
  await palette.getByRole('button').filter({ hasText: name }).click();
  await expect(page.getByLabel('搜索企业名称')).toBeVisible();
  await expect(page.getByRole('listitem').filter({ hasText: name })).toBeVisible();
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(detail.drafts.every((draft: { status: string }) => draft.status === 'draft')).toBe(true);
  expect(
    (await (await request.get(`/api/books/${book.id}/exports`, { headers })).json()).exports,
  ).toEqual([]);
});

test('手机Agent导航保持紧凑首屏、焦点与背景滚动，后台页仅显示真实持久任务', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home');
  await expect(page.getByRole('heading', { name: '事务所管理', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toHaveCount(0);
  expect((await page.locator('.agent-sidebar').boundingBox())!.height).toBeLessThan(100);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'artifacts/stage14-agent-workspace-mobile.png', fullPage: false });
  const before = await page.evaluate(() => document.body.style.overflow);
  await openNavigation(page);
  await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.body.style.overflow)).toBe(before);
  await expect(page.getByRole('button', { name: '打开工作空间导航', exact: true })).toBeFocused();
  await openManagement(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '后台运行', exact: true })
    .click();
  await expect(page.getByRole('heading', { name: /后台/ })).toBeVisible();
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const summary = await (
    await request.get('/api/agent-workspace/summary', {
      headers: { Authorization: `Bearer ${session.token}` },
    })
  ).json();
  expect(summary.mode).toBe('persisted_job_states');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'artifacts/stage14-agent-runtime-mobile.png', fullPage: false });
});
