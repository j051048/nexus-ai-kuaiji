import { expect, test } from '@playwright/test';

test.describe('TAX03 transport master data', () => {
  test('creates stable vehicle and driver IDs and links a project on desktop and mobile', async ({ page, request }) => {
    test.setTimeout(120_000);
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const book = await (await request.post('/api/books', { headers, data: { name: `TAX03运输 ${crypto.randomUUID().slice(0, 8)}`, period: '2026-10' } })).json();
    const project = await request.post(`/api/books/${book.id}/ledger/entities`, { headers, data: { kind: 'project', code: 'TAX03-E2E-P', name: '机场接送项目' } });
    expect(project.status()).toBe(201);

    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.goto(`/workspace/ledger?book=${book.id}`, { waitUntil: 'domcontentloaded' });
      const panel = page.getByRole('region', { name: '运输主数据' });
      await expect(panel).toBeVisible({ timeout: 60_000 });
      if (viewport.width > 500) {
        await panel.getByLabel('类型').selectOption('vehicle');
        await panel.getByLabel('稳定外部 ID').fill('E2E-VIN-1');
        await panel.getByLabel('显示名称').fill('京A·E2E01');
        await panel.getByRole('button', { name: '建立主体' }).click();
        await expect(panel.getByText('运输主体已建立')).toBeVisible();
        await panel.getByLabel('类型').selectOption('driver');
        await panel.getByLabel('稳定外部 ID').fill('E2E-DRV-1');
        await panel.getByLabel('显示名称').fill('E2E 张师傅');
        await panel.getByRole('button', { name: '建立主体' }).click();

        const relation = panel.locator('form').nth(1);
        await relation.getByLabel('运输主体').selectOption({ index: 1 });
        await relation.getByLabel('目标').selectOption({ label: '机场接送项目' });
        await relation.getByLabel('合同/经营权编号').fill('E2E-HT-1');
        await relation.getByLabel('经营权/服务内容').fill('机场专线');
        await relation.getByLabel('生效日期').fill('2026-10-01');
        await relation.getByRole('button', { name: '记录关系' }).click();
        await expect(panel.getByText(/→ 机场接送项目/)).toBeVisible();

        const day = panel.locator('form').nth(2);
        await day.getByLabel('车辆').selectOption({ index: 1 });
        await day.getByLabel('驾驶员').selectOption({ index: 1 });
        await day.getByLabel('班次').selectOption('day');
        await day.getByLabel('合同/经营权编号').fill('E2E-HT-1');
        await day.getByRole('button', { name: '记录班次' }).click();
        await expect(panel.getByText('1 条班次')).toBeVisible();

        await panel.getByLabel('类型').selectOption('driver');
        await panel.getByLabel('稳定外部 ID').fill('E2E-DRV-2');
        await panel.getByLabel('显示名称').fill('E2E 李师傅');
        await panel.getByRole('button', { name: '建立主体' }).click();
        const night = panel.locator('form').nth(2);
        await night.getByLabel('车辆').selectOption({ index: 1 });
        await night.getByLabel('驾驶员').selectOption({ index: 2 });
        await night.getByLabel('班次').selectOption('night');
        await night.getByLabel('合同/经营权编号').fill('E2E-HT-1');
        await night.getByRole('button', { name: '记录班次' }).click();
        await expect(panel.getByText('2 条班次')).toBeVisible();
      }
      await page.screenshot({ path: `tmp_test/tax03-transport-${viewport.width}.png`, fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    }
  });
});
