import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

test.describe.configure({ timeout: 120_000 });

async function fixture(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const tag = crypto.randomUUID().slice(0, 8);
  const created = await request.post('/api/books', {
    headers,
    data: { name: `流程编排验证企业 ${tag}`, period: '2026-11' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  expect(typeof book.company_id).toBe('string');
  return { book, headers, tag, companyId: book.company_id as string };
}

async function openStudio(page: Page, companyId: string) {
  await page.goto(`/admin/organizations/${companyId}?tab=workflow`);
  await expect(page.getByRole('heading', { name: '流程编排中台', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: '流程编排', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
}

async function startInstance(
  request: APIRequestContext,
  headers: Record<string, string>,
  book: { id: string; company_id: string },
  tag: string,
) {
  const response = await request.post('/api/workflow/instances', {
    headers,
    data: {
      company_id: book.company_id,
      book_id: book.id,
      subject_type: 'expense_reimbursement',
      subject_id: `合成报销-${tag}`,
      amount_cents: 100_000,
      process_kind: 'expense_reimbursement',
    },
  });
  expect(response.status()).toBe(200);
  return response.json();
}

test('工作流中台发布新版本、存量实例保旧版、挂起实例可人工接管 1366', async ({ page, request }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  const { book, headers, tag, companyId } = await fixture(request);
  await openStudio(page, companyId);
  await expect(page.locator('svg[aria-label="费用报销流程图"]')).toBeVisible();
  await expect(page.getByText('金额 < 5,000.00 元', { exact: true })).toBeVisible();

  // 首次发布：直接用内置模板发布第一版。
  await page.getByRole('button', { name: '保存并发布', exact: true }).click();
  await expect(page.getByText(/已发布新版本 v1/)).toBeVisible();
  await expect(page.locator('table.workflow-table tbody tr').first()).toContainText('v1');

  // 存量实例：锁定 v1，并且被挂起等待人工接管。
  const instance = await startInstance(request, headers, book, tag);
  expect(instance.definition_version).toBe(1);
  const suspended = await request.post(`/api/workflow/instances/${instance.id}/actions`, {
    headers,
    data: { action: 'suspend', reason: '等待业务部门补充发票' },
  });
  expect(suspended.status()).toBe(200);
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByText('锁定版本 v1', { exact: false }).first()).toBeVisible();
  await expect(page.getByText('挂起原因：等待业务部门补充发票')).toBeVisible();

  // 人工接管：恢复后实例继续进行。
  await page.getByRole('button', { name: '人工接管并恢复', exact: true }).click();
  await expect(page.getByText(/已人工接管并恢复办理/)).toBeVisible();

  // 发版 v2：只影响新实例，老实例继续按 v1 走完。
  await page.getByLabel('双人审批门槛（元，达到此金额需双人审批）').fill('3000');
  await page.getByLabel('变更说明').fill('合成验证：把双人审批门槛下调到 3,000 元');
  await page.getByRole('button', { name: '保存并发布', exact: true }).click();
  await expect(page.getByText(/已发布新版本 v2/)).toBeVisible();
  const rows = page.locator('table.workflow-table tbody tr');
  await expect(rows.filter({ hasText: '已归档' })).toHaveCount(1);
  await expect(rows.filter({ hasText: '生效中' })).toHaveCount(1);
  await expect(page.getByText('锁定版本 v1', { exact: false }).first()).toBeVisible();

  // 版本 Diff：v1 → v2 必须报出门槛变化。
  await page.getByLabel('起始版本').selectOption({ label: 'v1 · 已归档' });
  await page.getByLabel('目标版本').selectOption({ label: 'v2 · 生效中' });
  await page.getByRole('button', { name: '对比', exact: true }).click();
  await expect(page.getByText(/双人审批门槛：5,000.00 元 → 3,000.00 元/)).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: 'tmp_test/workflow-studio-1366.png', fullPage: true });
});

test('工作流中台在 390 窄屏可读且无横向溢出', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { companyId } = await fixture(request);
  await openStudio(page, companyId);
  await expect(page.locator('svg[aria-label="费用报销流程图"]')).toBeVisible();
  await expect(page.getByText('尚未创建版本，可直接用内置模板发布第一版。')).toBeVisible();
  await expect(page.getByRole('button', { name: '保存并发布', exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
  await page.screenshot({ path: 'tmp_test/workflow-studio-390.png', fullPage: true });
});
