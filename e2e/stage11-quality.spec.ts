import { openAccounting, openManagement } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('评测标准须两位会计核对，未覆盖不能算通过，评测不修改已审凭证', async ({ page, request }) => {
  // Complete two-person approval, standard confirmation and replay in one flow.
  test.setTimeout(60_000);
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=reviewer')).json();
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` },
    financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const context = await (
    await request.get('/api/business/context', {
      headers: { Authorization: `Bearer ${employee.token}` },
    })
  ).json();
  const company = context.companies[0],
    marker = crypto.randomUUID().slice(0, 8),
    name = `合成质量评测 ${marker}`,
    party = `虚构评测供方 ${marker}`;
  const bookResponse = await request.post('/api/books', {
    headers,
    data: { name, company_id: company.id, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-reviewer`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '明确合成的回放资料',
          rows: [
            {
              external_id: `quality-${marker}`,
              date: '2026-10-03',
              description: '办公用品',
              counterparty: party,
              amount: '9.00',
              direction: 'expense',
              evidence_ref: '合成归档票据，仅用于浏览器流程验证',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const ledger = await (await request.get(`/api/books/${book.id}`, { headers })).json(),
    draft = ledger.drafts[0];
  expect(
    (
      await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
        headers: financeHeaders,
        data: { expected_version: draft.version },
      })
    ).status(),
  ).toBe(200);
  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(name);
  await page.getByRole('listitem').filter({ hasText: name }).click();
  await openManagement(page);
  await page.getByRole('button', { name: '试点评测', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '用经过独立核对的业务，检验自动化。', exact: true }),
  ).toBeVisible();
  await page
    .locator('.quality-grid .quality-card')
    .first()
    .getByRole('button')
    .filter({ hasText: party })
    .click();
  await page
    .getByLabel('评测标准核对依据', { exact: true })
    .fill('按合成归档票据核对日期、金额、交易方和已审核分录');
  await page.getByRole('button', { name: '冻结并提议评测标准', exact: true }).click();
  await expect(page.getByRole('button', { name: '独立确认评测标准', exact: true })).toBeDisabled();
  const overview = await (await request.get(`/api/books/${book.id}/quality`, { headers })).json(),
    standard = overview.standards[0];
  expect(standard.confirmed).toBe(false);
  expect(
    (
      await request.post(`/api/books/${book.id}/quality/standards/${standard.id}/confirm`, {
        headers,
        data: { evidence_checked: true, reason: '自证应该被拒绝' },
      })
    ).status(),
  ).toBe(409);
  await page.getByLabel('切换验证身份').selectOption('reviewer');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(name);
  await page.getByRole('listitem').filter({ hasText: name }).click();
  await openManagement(page);
  await page.getByRole('button', { name: '试点评测', exact: true }).click();
  await page.locator('.quality-standard .quality-row').filter({ hasText: party }).click();
  await page
    .getByLabel('评测标准核对依据', { exact: true })
    .fill('作为另一位会计独立核对合成原件，确认此标准');
  await page.getByLabel('我已独立核对原件、字段答案及最终分录', { exact: true }).check();
  await page.getByRole('button', { name: '独立确认评测标准', exact: true }).click();
  await expect(page.locator('.quality-confirmed')).toContainText('另一位会计已确认');
  await page.getByLabel(`选择评测标准 ${party} 9.00`, { exact: true }).check();
  await page.getByRole('button', { name: '回放 1 个确认标准', exact: true }).click();
  await expect(page.getByLabel('评测回放报告')).toContainText('未覆盖');
  await expect(page.getByLabel('企业月份与业务类型评测汇总')).toContainText('日常收支');
  await expect(page.getByLabel('企业月份与业务类型评测汇总')).toContainText('尚无已测样本');
  const after = await (
    await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers: financeHeaders })
  ).json();
  expect(after.status).toBe('approved');
  expect(after.version).toBe(draft.version + 1);
  expect(
    (
      await request.get(`/api/books/${book.id}/quality`, {
        headers: { Authorization: `Bearer ${employee.token}` },
      })
    ).status(),
  ).toBe(404);
  await page.screenshot({ path: 'artifacts/stage11-quality.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
