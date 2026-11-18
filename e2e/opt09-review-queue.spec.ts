import { independentReviewer, pinReviewer } from './independent-review';
import { expect, test, type APIRequestContext } from '@playwright/test';

const period = '2026-11';

async function owner(request: APIRequestContext) {
  const response = await request.get('/api/session?persona=accountant');
  expect(response.status()).toBe(200);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

test('风险分档、原因与筛选来自真实可复核队列', async ({ page, request }) => {
  const headers = await owner(request);
  const marker = crypto.randomUUID().slice(0, 8);
  const created = await request.post('/api/books', {
    headers,
    data: { name: `000 OPT09 风险队列（虚构） ${marker}`, period },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const secondBookResponse = await request.post('/api/books', {
    headers,
    data: {
      name: `000 OPT09 风险队列乙（虚构） ${marker}`,
      period,
      company_id: book.company_id,
    },
  });
  expect(secondBookResponse.status()).toBe(201);
  const secondBook = await secondBookResponse.json();
  const imported = await request.post(`/api/books/${book.id}/imports`, {
    headers,
    data: {
      source_name: 'OPT09 明确虚构风险队列',
      rows: [
        {
          external_id: `opt09-clean:${marker}`,
          date: `${period}-03`,
          description: `办公用品有票据低风险 ${marker}`,
          counterparty: '虚构供应商甲',
          amount: '100.00',
          direction: 'expense',
          evidence_ref: '明确虚构的归档凭据 OPT09-1',
        },
      ],
    },
  });
  expect(imported.status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const importedMissing = await request.post(`/api/books/${secondBook.id}/imports`, {
    headers,
    data: {
      source_name: 'OPT09 明确虚构风险队列乙',
      rows: [
        {
          external_id: `opt09-missing:${marker}`,
          date: `${period}-04`,
          description: `办公用品缺票据高风险 ${marker}`,
          counterparty: '虚构供应商乙',
          amount: '88.00',
          direction: 'expense',
          evidence_ref: '',
        },
      ],
    },
  });
  expect(importedMissing.status()).toBe(200);
  expect(
    (await request.post(`/api/books/${secondBook.id}/suggestions`, { headers })).status(),
  ).toBe(200);
  await independentReviewer(request, headers, book.id);
  await independentReviewer(request, headers, secondBook.id);
  await pinReviewer(page);
  await page.goto('/workspace/review');
  await expect(
    page.getByRole('heading', { name: '集中复核，把时间留给判断。', exact: true }),
  ).toBeVisible();
  await page.getByLabel('复核期间', { exact: true }).fill(period);
  await page.getByLabel('复核企业', { exact: true }).selectOption(book.company_id);
  await expect(page.getByLabel('本页风险摘要', { exact: true })).toContainText('高风险 1');
  await expect(page.getByLabel('本页风险摘要', { exact: true })).toContainText('低风险 1');
  await expect(page.getByLabel('本页风险摘要', { exact: true })).toContainText('review-risk-v1');

  const highRow = page.getByRole('row', { name: new RegExp(`办公用品缺票据高风险 ${marker}`) });
  await expect(highRow).toContainText('高风险');
  await expect(highRow).toContainText(/证据/);
  const lowRow = page.getByRole('row', { name: new RegExp(`办公用品有票据低风险 ${marker}`) });
  await expect(lowRow).toContainText('低风险');
  await expect(lowRow).toContainText('无硬性异常');

  await page.getByLabel('复核风险分组', { exact: true }).selectOption('high');
  await expect(page).toHaveURL(/review_risk=high/);
  await expect(page.getByLabel('本页风险摘要', { exact: true })).toContainText('高风险 1');
  await expect(highRow).toBeVisible();
  await expect(lowRow).toHaveCount(0);
  await page.screenshot({ path: 'tmp_test/opt09-review-risk-1280.png', fullPage: true });

  const company = page.getByLabel('复核企业', { exact: true });
  await company.selectOption({ label: '青屿设计（虚构示例）' });
  await expect(highRow).toHaveCount(0);
  await company.selectOption('');
  await expect(highRow).toBeVisible();

  await highRow.getByRole('checkbox').check();
  await expect(page.getByText('已选 1 张', { exact: true })).toBeVisible();
  await page.getByLabel('复核风险分组', { exact: true }).selectOption('low');
  await expect(page.getByText('已选 0 张', { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page).toHaveURL(/review_risk=low/);
  await expect(lowRow).toBeVisible();
  await expect(highRow).toHaveCount(0);
  await page.screenshot({ path: 'tmp_test/opt09-review-risk-390.png', fullPage: true });
  await lowRow.getByRole('checkbox').check();
  await page.getByRole('button', { name: '预览所选凭证', exact: true }).click();
  await expect(page.getByRole('note').filter({ hasText: '不能在此直接撤销' })).toBeVisible();
});
