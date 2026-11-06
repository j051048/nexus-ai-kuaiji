import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import AxeBuilder from '@axe-core/playwright';

const longTask = {
  key: 'finance:long',
  kind: 'finance',
  ref: 'long',
  company_id: 'company-a',
  company_name: '虚构企业名称特别长用于检查换行与金额显示有限公司',
  book_id: 'book-a',
  event_id: null,
  draft_id: null,
  batch_id: null,
  title: '跨年度设备采购与安装服务费用报销（含质保金与负数冲销）',
  state: 'department_approved',
  priority: 1,
  due_on: null,
  updated_at: '2026-11-06T00:00:00Z',
  bucket: 'action',
  action_label: '核验资料',
  responsible: '企业财务',
  overdue: false,
  amount: '-123456789012.34',
};

const tasks = [
  longTask,
  { ...longTask, key: 'finance:blocked', ref: 'blocked', state: 'failed', title: '资料金额与原件不一致', amount: '88.00', bucket: 'action' },
];

async function mockTasks(page: Page) {
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
}

async function finance(page: Page) {
  await page.goto('/');
  await page.waitForTimeout(1200);
  await page.locator('select[aria-label="切换验证身份"]').selectOption('finance');
  await page.getByRole('heading', { name: '财务核验', exact: true }).waitFor({ timeout: 15000 });
  await expect(page.locator('article.workspace-task')).toHaveCount(2);
}

test.beforeEach(async ({ page }) => {
  await mockTasks(page);
});

test('IMP06 key list text uses at least 12px for actionable labels', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(1200);
  await page.locator('select[aria-label="切换验证身份"]').selectOption('employee');
  await page.getByRole('button', { name: '我的事项', exact: true }).click();
  await page.waitForTimeout(1200);
  const sizes = await page.evaluate(() => {
    const find = (sel: string) => {
      const el = document.querySelector(sel);
      return el ? parseFloat(getComputedStyle(el).fontSize) : null;
    };
    return {
      tab: find('.list-tabs [role="tab"]'),
      empty: find('.empty-state p'),
      footer: find('.workspace-footer'),
    };
  });
  expect(sizes.tab).toBeGreaterThanOrEqual(12);
  expect(sizes.empty).toBeGreaterThanOrEqual(12);
  expect(sizes.footer).toBeGreaterThanOrEqual(12);
});

test('IMP06 long and negative amount is complete on desktop and mobile', async ({ page }) => {
  await finance(page);
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.waitForTimeout(300);
  const desktop = page.locator('article.workspace-task').first();
  await expect(desktop.locator('.workspace-task-amount')).toContainText('¥-123,456,789,012.34');
  const support = await page.evaluate(() => ({
    guidance: parseFloat(getComputedStyle(document.querySelector('details.workspace-task-help summary')!).fontSize),
    state: parseFloat(getComputedStyle(document.querySelector('.workspace-task-state small')!).fontSize),
    statusText: document.querySelector('.workspace-state-pill')?.textContent?.trim() ?? '',
  }));
  expect(support.guidance).toBeGreaterThanOrEqual(12);
  expect(support.state).toBeGreaterThanOrEqual(12);
  expect(support.statusText.length).toBeGreaterThan(0);
  const desktopOverflow = await desktop.evaluate((row) => {
    const amount = row.querySelector('.workspace-task-amount');
    return amount ? amount.scrollWidth - amount.clientWidth : 999;
  });
  expect(desktopOverflow).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileAmount = page.locator('article.workspace-task').first().locator('.workspace-task-mobile-amount');
  await expect(mobileAmount).toBeVisible();
  await expect(mobileAmount).toContainText('¥-123,456,789,012.34');
  const metrics = await page.evaluate(() => {
    const row = document.querySelector('article.workspace-task');
    const amount = row?.querySelector('.workspace-task-mobile-amount');
    return {
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
      amountOverflow: amount ? amount.scrollWidth - amount.clientWidth : 999,
      amountText: amount?.textContent,
    };
  });
  fs.writeFileSync('tmp_test/imp/imp-20261106-a/imp06-amount-metrics.json', JSON.stringify(metrics, null, 2));
  expect(metrics.pageOverflow).toBeLessThanOrEqual(0);
  expect(metrics.amountOverflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: 'tmp_test/imp/imp-20261106-a/imp06-mobile-long-amount.png', fullPage: false });

  // 200% browser zoom on a 1366px window yields an effective 683px layout viewport.
  await page.setViewportSize({ width: 683, height: 450 });
  await page.waitForTimeout(300);
  const zoomed = await page.evaluate(() => {
    const amount = document.querySelector('article.workspace-task .workspace-task-mobile-amount');
    return {
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
      visible: amount ? getComputedStyle(amount).display !== 'none' : false,
      text: amount?.textContent ?? '',
    };
  });
  expect(zoomed.pageOverflow).toBeLessThanOrEqual(0);
  expect(zoomed.visible).toBe(true);
  expect(zoomed.text).toContain('¥-123,456,789,012.34');
});


test('IMP06 tokens reach the lazy intake dialog and mobile inputs stay 16px', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(1200);
  await page.locator('select[aria-label="切换验证身份"]').selectOption('employee');
  await page.getByRole('button', { name: /提交报销/ }).click();
  await page.waitForTimeout(800);
  await page.locator('.intake-modal').waitFor({ state: 'visible' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const values = await page.evaluate(() => {
    const input = document.querySelector('.intake-fields input');
    const amount = document.querySelector('.intake-fields input[inputmode="decimal"]') || input;
    return {
      base: getComputedStyle(document.documentElement).getPropertyValue('--font-size-base').trim(),
      input: amount ? getComputedStyle(amount).fontSize : null,
      dialogFont: getComputedStyle(document.querySelector('.intake-modal')!).fontSize,
    };
  });
  expect(values.base).toBe('14px');
  expect(values.dialogFont).toBe('14px');
  expect(values.input).toBe('16px');
  await page.keyboard.press('Escape');
});


test('IMP06 help opens from the keyboard and key list has no new axe violations', async ({ page }, info) => {
  await finance(page);
  const summary = page.locator('article.workspace-task').first().locator('details.workspace-task-help summary');
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('article.workspace-task').first().locator('p.workspace-task-guidance')).toBeVisible();
  const scan = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
    .analyze();
  await info.attach('imp06-axe', { body: JSON.stringify(scan.violations, null, 2), contentType: 'application/json' });
  expect(scan.violations).toEqual([]);
});
