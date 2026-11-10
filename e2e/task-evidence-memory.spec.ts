import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import { independentReviewer } from './independent-review';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

test.setTimeout(90_000);
const output = 'tmp_test/task-evidence-0406-20261110';
const original = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));
async function data(response: APIResponse, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return response.json();
}
async function identity(request: APIRequestContext, persona: string) {
  const session = await data(await request.get(`/api/session?persona=${persona}`));
  return { Authorization: `Bearer ${session.token}` };
}
async function reviewedRule(request: APIRequestContext) {
  const owner = await identity(request, 'accountant'),
    employee = await identity(request, 'employee');
  const finance = await identity(request, 'finance'),
    department = await identity(request, 'department');
  const context: BusinessContext = await data(
    await request.get('/api/business/context', { headers: employee }),
  );
  const company = context.companies.find((c) => c.can_submit && c.department_id)!;
  const tag = crypto.randomUUID().slice(0, 8),
    party = `虚构规则供方-${tag}`;
  const book = await data(
    await request.post('/api/books', {
      headers: owner,
      data: { company_id: company.id, name: `规则依据-${tag}`, period: '2026-10' },
    }),
    201,
  );
  await data(
    await request.put(`/api/books/${book.id}/members/local-business-finance`, {
      headers: owner,
      data: { role: 'editor' },
    }),
  );
  const reviewer = await independentReviewer(request, owner, book.id);
  await data(
    await request.post(`/api/books/${book.id}/ledger/entities`, {
      headers: owner,
      data: {
        kind: 'counterparty',
        code: `MEM-${tag}`,
        name: party,
      },
    }),
    201,
  );
  let event: BusinessEvent = await data(
    await request.post('/api/business/events', {
      headers: employee,
      data: {
        company_id: company.id,
        department_id: company.department_id,
        template_id: 'procurement_expense',
        payload: {
          occurred_on: '2026-10-12',
          description: `虚构耗材-${tag}`,
          counterparty: party,
          amount: '100.00',
          direction: 'expense',
          payment_status: 'paid',
          category: 'office',
        },
      },
    }),
    201,
  );
  event = await data(
    await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers: employee,
        multipart: { file: { name: '虚构票据.png', mimeType: 'image/png', buffer: original } },
      },
    ),
  );
  for (const [action, headers] of [
    ['submit', employee],
    ['department-approve', department],
    ['finance-approve', finance],
  ] as const)
    event = await data(
      await request.post(`/api/business/events/${event.id}/${action}`, {
        headers,
        data: { expected_version: event.version },
      }),
    );
  const mapped = await data(
    await request.post(`/api/business/events/${event.id}/handoff`, {
      headers: finance,
      data: { expected_version: event.version, book_id: book.id, idempotency_key: `memory-${tag}` },
    }),
  );
  const base = `/api/books/${book.id}`;
  let draft = await data(
    await request.get(`${base}/drafts/${mapped.draft_id}`, { headers: owner }),
  );
  draft = await data(
    await request.patch(`${base}/drafts/${draft.id}`, {
      headers: owner,
      data: {
        expected_version: draft.version,
        description: '人工核实办公用途',
        correction_reason: '虚构原件逐项核对',
        lines: [
          { account_code: '6602.99', debit: '100.00', credit: '0.00' },
          { account_code: '1002', debit: '0.00', credit: '100.00' },
        ],
      },
    }),
  );
  const checks = await data(
    await request.post(`/api/business/events/${event.id}/reconcile`, { headers: owner }),
  );
  for (const finding of checks.findings.filter((f: { code: string }) =>
    f.code.startsWith('ocr_missing_'),
  ))
    await data(
      await request.post(`/api/intelligence/findings/${finding.id}/resolve`, {
        headers: owner,
        data: { reason: '合成测试原件已人工核对，不调用真实 OCR 或模型。' },
      }),
    );
  await data(
    await request.post(`${base}/drafts/${draft.id}/approve`, {
      headers: reviewer,
      data: { expected_version: draft.version },
    }),
  );
  const learning = await data(await request.get(`${base}/learning`, { headers: owner }));
  expect(learning.rules).toHaveLength(1);
  return { book, company, owner, rule: learning.rules[0], party };
}

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test.describe(device, () => {
    test.use({ viewport });
    test('规则候选读取真实审核来源，过时响应不显示也不自动发布', async ({ page, request }) => {
      const { book, owner, rule, party } = await reviewedRule(request);
      let writes = 0;
      page.on('request', (req) => {
        if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method())) writes++;
      });
      await page.goto(`/workspace/learning?book=${book.id}`);
      await page.locator('.learning-rule-choice').filter({ hasText: party }).click();
      await page.getByText('审核来源与适用范围', { exact: true }).click();
      const panel = page.getByRole('region', { name: '规则审核依据' });
      await expect(panel).toContainText('规则候选，尚未发布');
      await expect(panel).toContainText('可核对审核来源：1 项');
      await expect(panel).toContainText('历史金额不作为当前事实');
      await expect(panel).toContainText('2026-10');
      expect(writes).toBe(0);
      expect(
        (
          await data(
            await request.get(`/api/books/${book.id}/learning/rules/${rule.id}`, {
              headers: owner,
            }),
          )
        ).status,
      ).toBe('draft');
      mkdirSync(output, { recursive: true });
      await page.screenshot({ path: `${output}/${device}-rule-memory.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.getByText('审核来源与适用范围', { exact: true }).click();
      await page.route('**/learning/rules/*/memory?*', async (route) => {
        const response = await route.fetch();
        await route.fulfill({ json: { ...(await response.json()), version: 999 } });
      });
      await page.getByText('审核来源与适用范围', { exact: true }).click();
      await expect(page.locator('.rule-memory').getByRole('alert')).toBeVisible();
      await expect(panel).toHaveCount(0);
      expect(writes).toBe(0);
    });
    test('只读并行查询实际接口，结果重新核对后才展示', async ({ page, request }) => {
      const owner = await identity(request, 'accountant');
      const book = await data(
        await request.post('/api/books', {
          headers: owner,
          data: { name: `并行查询-${crypto.randomUUID()}`, period: '2026-10' },
        }),
        201,
      );
      await page.goto(
        `/workspace/delivery?book=${book.id}&company=${book.company_id}&period=2026-10`,
      );
      const panel = page.getByRole('region', { name: '交付依据调查' });
      const response = page.waitForResponse(
        (r) => new URL(r.url()).pathname === '/api/assistant/investigations',
      );
      await panel.getByRole('button', { name: '核对交付依据' }).click();
      const result = await (await response).json();
      expect(result.execution).toMatchObject({
        parallelism: 2,
        attempted_reads: 3,
        validation_reads: 3,
        model_calls: 0,
        cost_cny: null,
      });
      await expect(panel).toContainText('已核对 3 / 3');
      await expect(panel).toContainText('仍须按原流程人工办理');
      await panel.scrollIntoViewIfNeeded();
      mkdirSync(output, { recursive: true });
      await page.screenshot({ path: `${output}/${device}-parallel-evidence.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  });
}
