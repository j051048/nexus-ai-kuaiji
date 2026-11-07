import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const tasks = [
  {
    key: 'finance:a',
    kind: 'finance',
    ref: 'a',
    company_id: 'company-a',
    company_name: '虚构企业甲',
    book_id: 'book-a',
    event_id: null,
    draft_id: null,
    batch_id: null,
    title: '',
    state: 'department_approved',
    priority: 1,
    due_on: null,
    updated_at: '2026-11-06T00:00:00Z',
    bucket: 'action',
    action_label: '核验资料',
    responsible: '企业财务',
    overdue: false,
    amount: '120.00',
  },
  {
    key: 'finance:b',
    kind: 'finance',
    ref: 'b',
    company_id: 'company-a',
    company_name: '虚构企业甲',
    book_id: 'book-a',
    event_id: null,
    draft_id: null,
    batch_id: null,
    title: '资料金额与原件不一致',
    state: 'failed',
    priority: 1,
    due_on: null,
    updated_at: '2026-11-06T00:00:00Z',
    bucket: 'action',
    action_label: '查看差异',
    responsible: '企业财务',
    overdue: false,
    amount: '88.00',
  },
];

async function finance(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.waitForTimeout(1200);
  await page.locator('select[aria-label="切换验证身份"]').selectOption('finance');
  await page.getByRole('heading', { name: '财务核验', exact: true }).waitFor({ timeout: 15000 });
  await expect(page.locator('article.workspace-task')).toHaveCount(2);
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/workspace/tasks?**', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tasks,
        counts: { action: 2, waiting: 0 },
        pagination: { total: 2, limit: 20, offset: 0, has_more: false },
      }),
    });
  });
});

test('IMP03 desktop keeps ordinary guidance collapsed, blocked reason visible, and fallback title readable', async ({
  page,
}) => {
  await finance(page);
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.waitForTimeout(400);
  const rows = page.locator('article.workspace-task');
  await expect(rows.first().locator('h3')).toContainText('财务核验');
  const firstBox = await rows.first().boundingBox();
  expect(firstBox?.y ?? 9999).toBeLessThan(700);
  await page.screenshot({ path: test.info().outputPath('imp03-desktop.png'), fullPage: false });
  const metrics = await page.evaluate(() => ({
    rows: [...document.querySelectorAll('article.workspace-task')].map((row) => ({
      height: row.getBoundingClientRect().height,
      title: row.querySelector('h3')?.textContent,
      details: row.querySelectorAll('details.workspace-task-help').length,
      guidance: row.querySelectorAll(':scope > .workspace-task-main > p.workspace-task-guidance')
        .length,
    })),
    firstTop: document.querySelector('article.workspace-task')?.getBoundingClientRect().top,
    viewport: window.innerWidth,
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  }));
  fs.writeFileSync(
    test.info().outputPath('imp03-desktop-metrics.json'),
    JSON.stringify(metrics, null, 2),
  );
  await expect(rows.first().locator('h3')).not.toContainText('550e8400');
  const ordinary = rows.first();
  await expect(ordinary.locator('details.workspace-task-help')).toHaveCount(1);
  await expect(ordinary.locator('p.workspace-task-guidance')).toBeHidden();
  await ordinary.locator('details.workspace-task-help summary').click();
  await expect(ordinary.locator('p.workspace-task-guidance')).toBeVisible();
  const blocked = rows.nth(1);
  await expect(blocked.locator('p.workspace-task-guidance')).toBeVisible();
  await expect(blocked.locator('p.workspace-task-guidance')).toContainText('重新读取');
  await expect(blocked.locator('details.workspace-task-help')).toHaveCount(0);
  await expect(page.locator('h2', { hasText: '需要我处理' })).toContainText('2');
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test('IMP03 mobile keeps the same reason and action without horizontal overflow', async ({
  page,
}) => {
  await finance(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const rows = page.locator('article.workspace-task');
  await expect(rows.first().locator('h3')).toContainText('财务核验');
  const mobileBox = await rows.first().boundingBox();
  expect(mobileBox?.y ?? 9999).toBeLessThan(800);
  await page.screenshot({ path: test.info().outputPath('imp03-mobile.png'), fullPage: false });
  await rows.first().locator('details.workspace-task-help summary').click();
  await expect(rows.first().locator('p.workspace-task-guidance')).toBeVisible();
  await expect(rows.first().getByRole('button', { name: '核验资料' })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
