import { openAccounting } from './ui-navigation';
import { expect, test, type APIRequestContext } from '@playwright/test';
const image = { name: '合成付款票据.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') };

async function prepare(request: APIRequestContext) {
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['employee', 'department', 'finance', 'accountant']) { const session = await (await request.get(`/api/session?persona=${persona}`)).json(); headers[persona] = { Authorization: `Bearer ${session.token}` }; }
  const context = await (await request.get('/api/business/context', { headers: headers.employee })).json(); const company = context.companies[0];
  let response = await request.post('/api/business/events', { headers: headers.employee, data: { company_id: company.id, template_id: 'procurement_expense', payload: { occurred_on: '2026-10-10', description: '第九阶段分笔付款核对', counterparty: '第九阶段虚构供应商', amount: '100.00', direction: 'expense', payment_status: 'paid', category: 'office', purpose: '虚构采购分笔付款' } } }); expect(response.status()).toBe(201); let event = await response.json();
  response = await request.post(`/api/business/events/${event.id}/attachments?expected_version=${event.version}`, { headers: headers.employee, multipart: { file: image } }); expect(response.status()).toBe(200); event = await response.json();
  for (const [action, actor] of [['submit', 'employee'], ['department-approve', 'department'], ['finance-approve', 'finance']]) { response = await request.post(`/api/business/events/${event.id}/${action}`, { headers: headers[actor], data: { expected_version: event.version } }); expect(response.status()).toBe(200); event = await response.json(); }
  const workbench = await (await request.get('/api/workbench', { headers: headers.finance })).json(); const book = workbench.books.find((item: { company_id: string }) => item.company_id === company.id);
  response = await request.post(`/api/business/events/${event.id}/handoff`, { headers: headers.finance, data: { expected_version: event.version, book_id: book.id, idempotency_key: crypto.randomUUID() } }); expect(response.status()).toBe(200); const mapping = await response.json();
  const suffix = Date.now().toString();
  response = await request.post(`/api/books/${book.id}/imports`, { headers: headers.accountant, data: { source_name: '第九阶段虚构银行文件', rows: ['40.00', '60.00'].map((amount, index) => ({ external_id: `stage9-match-${suffix}-${index}`, date: '2026-10-10', description: `第九阶段付款回单 ${index + 1}`, counterparty: '第九阶段虚构供应商', amount, direction: 'expense', evidence_ref: '虚构回单，仅验证流程' })) } }); expect(response.status()).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers: headers.accountant })).status()).toBe(200);
  return { headers, book, event, mapping, suffix };
}

test('真实后台分笔流水核对：金额精确相加、保存关联回执、保留会计复核与来源隔离', async ({ page, request }) => {
  const { headers, book, event, mapping, suffix } = await prepare(request);
  await page.goto('/'); await openAccounting(page); await page.getByLabel('搜索企业名称').fill(book.name); await page.getByRole('listitem').filter({ hasText: book.name }).click();
  await page.getByRole('button', { name: '流水与业务匹配', exact: true }).click();
  await expect(page.getByRole('heading', { name: '让一笔业务，对上一笔付款。', exact: true })).toBeVisible();
  await page.getByRole('button').filter({ hasText: '第九阶段分笔付款核对' }).click();
  await page.getByLabel(`选择核对流水 stage9-match-${suffix}-0`, { exact: true }).check();
  await page.getByLabel('流水关联核对依据', { exact: true }).fill('已核对同一采购事项的两次付款回单');
  await expect(page.getByRole('button', { name: '确认关联并保存回执', exact: true })).toBeDisabled();
  await page.getByLabel(`选择核对流水 stage9-match-${suffix}-1`, { exact: true }).check();
  await expect(page.getByRole('button', { name: '确认关联并保存回执', exact: true })).toBeEnabled();
  await page.screenshot({ path: 'artifacts/stage9-reconciliation-confirm.png', fullPage: true });
  await page.getByRole('button', { name: '确认关联并保存回执', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('关联回执已保存');
  const matched = await (await request.get(`/api/reconciliation/books/${book.id}/events?status=matched`, { headers: headers.accountant })).json();
  expect(matched.events.find((item: { event_id: string }) => item.event_id === event.id).links).toHaveLength(2);
  const draft = await (await request.get(`/api/books/${book.id}/drafts/${mapping.draft_id}`, { headers: headers.accountant })).json(); expect(draft.status).toBe('draft');
  expect((await request.get(`/api/reconciliation/books/${book.id}/events`, { headers: headers.employee })).status()).toBe(404);
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
