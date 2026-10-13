import { openAccounting, openBookTools } from './ui-navigation';
import { expect, test, type APIRequestContext } from '@playwright/test';

type Headers = { Authorization: string };
type Draft = { id: string; source_id: string; version: number; status: string; description: string; lines: { account_code: string; debit: string; credit: string }[] };
const period = '2026-11';

async function owner(request: APIRequestContext): Promise<Headers> {
  const response = await request.get('/api/session?persona=accountant'); expect(response.status()).toBe(200);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}
async function book(request: APIRequestContext, headers: Headers, name: string) {
  const response = await request.post('/api/books', { headers, data: { name, period } }); expect(response.status()).toBe(201);
  return await response.json() as { id: string; name: string; company_id: string };
}
async function drafts(request: APIRequestContext, headers: Headers, bookId: string, rows: { external_id: string; description: string; counterparty: string }[]): Promise<Draft[]> {
  const imported = await request.post(`/api/books/${bookId}/imports`, { headers, data: { source_name: '第八阶段明确虚构的验证流水', rows: rows.map(row => ({ ...row, date: `${period}-03`, amount: '113.00', direction: 'expense', evidence_ref: `明确虚构的归档凭据 ${row.external_id}` })) } });
  expect(imported.status()).toBe(200);
  const suggested = await request.post(`/api/books/${bookId}/suggestions`, { headers }); expect(suggested.status()).toBe(200);
  const response = await request.get(`/api/books/${bookId}`, { headers }); expect(response.status()).toBe(200);
  return (await response.json()).drafts;
}

test('真实 API 与 UI 跨企业复核：明确预览、人工确认、账套回执和员工隔离', async ({ page, request }) => {
  const headers = await owner(request), marker = crypto.randomUUID().slice(0, 8);
  const first = await book(request, headers, `000 第八阶段批量甲（虚构） ${marker}`);
  const second = await book(request, headers, `000 第八阶段批量乙（虚构） ${marker}`);
  const firstDescription = `办公费用批量甲 ${marker}`, secondDescription = `办公费用批量乙 ${marker}`;
  const [firstDraft] = await drafts(request, headers, first.id, [{ external_id: `batch-a:${marker}`, description: firstDescription, counterparty: '虚构商店甲' }]);
  const [secondDraft] = await drafts(request, headers, second.id, [{ external_id: `batch-b:${marker}`, description: secondDescription, counterparty: '虚构商店乙' }]);
  await page.goto('/');
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '跨企业批量复核', exact: true }).click();
  await expect(page.getByRole('heading', { name: '集中复核，把时间留给判断。', exact: true })).toBeVisible();
  await page.getByLabel('复核期间', { exact: true }).fill(period);
  await page.getByLabel(`选择凭证 ${firstDescription}`, { exact: true }).check();
  await page.getByLabel(`选择凭证 ${secondDescription}`, { exact: true }).check();
  const previewResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/review/previews' && response.request().method() === 'POST');
  await page.getByRole('button', { name: '预览所选凭证', exact: true }).click();
  const response = await previewResponse; expect(response.status()).toBe(201); const preview = await response.json();
  expect(preview.items).toHaveLength(2); expect(preview.ready_count).toBe(2); expect(preview.blocked_count).toBe(0);
  expect(new Set(preview.items.map((item: { company_id: string }) => item.company_id)).size).toBe(2);
  const pane = page.getByLabel('批量复核预览', { exact: true });
  await expect(pane).toContainText(first.name); await expect(pane).toContainText(second.name);
  await expect(pane.getByRole('button', { name: '确认批量复核', exact: true })).toBeDisabled();
  for (const [item, draft] of [[first, firstDraft], [second, secondDraft]] as const) {
    const before = await request.get(`/api/books/${item.id}/drafts/${draft.id}`, { headers }); expect((await before.json()).status).toBe('draft');
  }
  await page.getByLabel('确认已核对批量凭证证据与分录', { exact: true }).check();
  const confirmation = page.waitForResponse(response => new URL(response.url()).pathname === `/api/review/previews/${preview.id}/confirm` && response.request().method() === 'POST');
  await pane.getByRole('button', { name: '确认批量复核', exact: true }).click();
  const confirmed = await confirmation; expect(confirmed.status()).toBe(200); const receipt = await confirmed.json();
  expect(receipt.status).toBe('completed'); expect(receipt.approved_count).toBe(2); expect(receipt.failed_book_count).toBe(0);
  await expect(page.getByLabel('批量复核回执', { exact: true })).toContainText('通过 2 张');
  const repeated = await request.post(`/api/review/previews/${preview.id}/confirm`, { headers, data: confirmed.request().postDataJSON() }); expect(repeated.status()).toBe(200);
  expect((await repeated.json()).approved_count).toBe(2);
  for (const [item, draft] of [[first, firstDraft], [second, secondDraft]] as const) {
    const after = await request.get(`/api/books/${item.id}/drafts/${draft.id}`, { headers }); expect((await after.json()).status).toBe('approved');
    const learning = await request.get(`/api/books/${item.id}/learning`, { headers }); expect((await learning.json()).pagination.total).toBe(1);
  }
  await page.screenshot({ path: 'artifacts/stage8-cross-company-review.png', fullPage: true });
  await page.getByLabel('切换验证身份').selectOption('employee');
  await expect(page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '跨企业批量复核', exact: true })).toHaveCount(0);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const hidden = await request.get(`/api/books/${first.id}/learning`, { headers: { Authorization: `Bearer ${employee.token}` } }); expect(hidden.status()).toBe(404);
});

test('真实审核样例与规则 UI：原最终分录、真实反例评估、显式发布和新金额草稿', async ({ page, request }) => {
  const headers = await owner(request), marker = crypto.randomUUID().slice(0, 8);
  const current = await book(request, headers, `第八阶段企业学习验证（虚构） ${marker}`);
  const positiveParty = `虚构正例商店 ${marker}`, negativeParty = `虚构反例商店 ${marker}`;
  const values = await drafts(request, headers, current.id, [
    { external_id: `learn-positive:${marker}`, description: `办公正例 ${marker}`, counterparty: positiveParty },
    { external_id: `learn-negative:${marker}`, description: `办公反例 ${marker}`, counterparty: negativeParty },
  ]);
  const positive = values.find(item => item.description === `办公正例 ${marker}`)!, negative = values.find(item => item.description === `办公反例 ${marker}`)!;
  const correction = `已核对虚构合同用途，正例计入其他管理费用 ${marker}`;
  const updated = await request.patch(`/api/books/${current.id}/drafts/${positive.id}`, { headers, data: { expected_version: positive.version, description: `经会计核实的正例 ${marker}`, correction_reason: correction,
    lines: [{ account_code: '6602.99', debit: '113.00', credit: '0.00' }, { account_code: '1002', debit: '0.00', credit: '113.00' }] } }); expect(updated.status()).toBe(200);
  for (const draft of [await updated.json(), negative]) {
    const reviewed = await request.post(`/api/books/${current.id}/drafts/${draft.id}/approve`, { headers, data: { expected_version: draft.version } }); expect(reviewed.status()).toBe(200);
  }
  const initial = await (await request.get(`/api/books/${current.id}/learning`, { headers })).json();
  expect(initial.samples).toHaveLength(2); expect(initial.rules).toHaveLength(1); expect(initial.rules[0].status).toBe('draft');
  const ruleId = initial.rules[0].id, negativeSample = initial.samples.find((sample: { counterparty: string }) => sample.counterparty === negativeParty);
  await page.goto('/'); await openAccounting(page); await page.getByLabel('搜索企业名称').fill(marker);
  await page.getByRole('listitem').filter({ hasText: current.name }).click();
  await openBookTools(page); await page.getByRole('button', { name: '审核样例与规则', exact: true }).click();
  await expect(page.getByRole('heading', { name: '把修正，变成下一次的依据。', exact: true })).toBeVisible();
  const sample = page.locator('.learning-sample').filter({ hasText: positiveParty });
  await sample.getByRole('button', { name: '查看原处理与修正', exact: true }).click();
  const details = page.getByRole('dialog', { name: '已审核处理依据', exact: true });
  await expect(details.getByText('6602.02', { exact: true })).toBeVisible(); await expect(details.getByText('6602.99', { exact: true })).toBeVisible();
  await expect(details).toContainText(correction); await details.getByRole('button', { name: '关闭', exact: true }).click();
  await page.locator('.learning-rule-choice').filter({ hasText: positiveParty }).click();
  await expect(page.getByRole('button', { name: '填写依据并发布', exact: true })).toBeDisabled();
  await page.getByLabel(`选为反例 ${negativeParty} ${negativeSample.id}`, { exact: true }).check();
  const evaluationResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/books/${current.id}/learning/rules/${ruleId}/evaluate` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '评估正例与 1 条反例', exact: true }).click();
  const evaluated = await evaluationResponse; expect(evaluated.status()).toBe(200); const evaluation = await evaluated.json();
  expect(evaluation.passed).toBe(true); expect(evaluation.positive_count).toBe(1); expect(evaluation.negative_count).toBe(1); expect(evaluation.scanned_count).toBe(2);
  await expect(page.getByText('已通过当前样例评估', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '填写依据并发布', exact: true }).click();
  const publish = page.getByRole('dialog', { name: '发布经评估的企业规则', exact: true });
  await expect(publish.getByRole('button', { name: '确认发布', exact: true })).toBeDisabled();
  await publish.getByLabel('规则发布依据', { exact: true }).fill(`真实虚构样例正反例均核对，限定本账套及本期间 ${marker}`);
  const publishedResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/books/${current.id}/learning/rules/${ruleId}/publish` && response.request().method() === 'POST');
  await publish.getByRole('button', { name: '确认发布', exact: true }).click();
  const published = await publishedResponse; expect(published.status()).toBe(200); expect((await published.json()).status).toBe('published');
  await expect(publish).toHaveCount(0); await expect(page.locator('.learning-rule-choice').filter({ hasText: positiveParty })).toContainText('已发布');
  await page.getByRole('button', { name: '查看版本历史', exact: true }).click();
  const history = page.getByRole('dialog', { name: '企业规则版本历史', exact: true }); await expect(history).toContainText('v1'); await history.getByRole('button', { name: '关闭', exact: true }).click();
  await page.screenshot({ path: 'artifacts/stage8-reviewed-company-rules.png', fullPage: true });
  const nextImport = await request.post(`/api/books/${current.id}/imports`, { headers, data: { source_name: '已发布规则的新金额验证', rows: [{ external_id: `learn-next:${marker}`, date: `${period}-08`, description: '无演示分类关键词', counterparty: positiveParty, amount: '27.01', direction: 'expense', evidence_ref: '明确虚构的新交易凭据' }] } }); expect(nextImport.status()).toBe(200);
  const nextSuggested = await request.post(`/api/books/${current.id}/suggestions`, { headers }); expect(nextSuggested.status()).toBe(200);
  const after = await (await request.get(`/api/books/${current.id}`, { headers })).json(); const source = after.sources.find((row: { external_id: string }) => row.external_id === `learn-next:${marker}`);
  const next = after.drafts.find((row: Draft) => row.source_id === source.id);
  expect(next.status).toBe('draft'); expect(next.lines[0].account_code).toBe('6602.99'); expect(next.lines[0].debit).toBe('27.01');
});
