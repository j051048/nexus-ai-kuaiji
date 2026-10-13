import { expect, test, type APIRequestContext } from '@playwright/test';

const image = { name: 'stage8补件原件.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') };
async function setup(request: APIRequestContext) {
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['employee', 'department', 'finance', 'accountant']) { const session = await (await request.get(`/api/session?persona=${persona}`)).json(); headers[persona] = { Authorization: `Bearer ${session.token}` }; }
  const context = await (await request.get('/api/business/context', { headers: headers.employee })).json(); const company = context.companies[0];
  let response = await request.post('/api/business/events', { headers: headers.employee, data: { company_id: company.id, template_id: 'reimbursement', payload: { occurred_on: '2026-10-10', description: '第八阶段员工补件验证', counterparty: '虚构办公商店', amount: '100.00', direction: 'expense', payment_status: 'unpaid', category: 'office', purpose: '虚构办公耗材' } } });
  expect(response.status()).toBe(201); let event = await response.json();
  response = await request.post(`/api/business/events/${event.id}/attachments?expected_version=${event.version}`, { headers: headers.employee, multipart: { file: image } }); expect(response.status()).toBe(200); event = await response.json();
  for (const [action, actor] of [['submit', 'employee'], ['department-approve', 'department'], ['finance-approve', 'finance']]) { response = await request.post(`/api/business/events/${event.id}/${action}`, { headers: headers[actor], data: { expected_version: event.version } }); expect(response.status()).toBe(200); event = await response.json(); }
  const books = await (await request.get('/api/workbench', { headers: headers.finance })).json(); const book = books.books.find((row: { company_id: string }) => row.company_id === company.id);
  response = await request.post(`/api/business/events/${event.id}/handoff`, { headers: headers.finance, data: { expected_version: event.version, book_id: book.id, idempotency_key: crypto.randomUUID() } }); expect(response.status()).toBe(200); const mapping = await response.json();
  response = await request.post('/api/cases', { headers: headers.accountant, data: { event_id: event.id, kind: 'supplement', title: '第八阶段补充合同用途', reason: '请说明采购用途并补充清晰票据原件', required_fields: ['业务用途'], attachment_required: true, idempotency_key: crypto.randomUUID() } }); expect(response.status()).toBe(201); const task = await response.json();
  response = await request.post('/api/cases', { headers: headers.accountant, data: { event_id: event.id, kind: 'exception', title: '会计内部交易调查不可见', reason: '内部核对不向员工发布', idempotency_key: crypto.randomUUID() } }); expect(response.status()).toBe(201);
  return { headers, event, mapping, task };
}

test('真实补件站内闭环：员工只见本人公开请求，追加原件经过核验并保留会计复核', async ({ page, request }) => {
  const { headers, event, mapping, task } = await setup(request);
  await page.goto('/'); await page.getByLabel('切换验证身份').selectOption('employee');
  await page.getByRole('button', { name: '异常与补件', exact: true }).click();
  await expect(page.getByText('会计内部交易调查不可见', { exact: true })).toHaveCount(0);
  await page.getByRole('button').filter({ hasText: '第八阶段补充合同用途' }).click();
  await expect(page.getByRole('heading', { name: '第八阶段补充合同用途', exact: true })).toBeVisible();
  await page.getByLabel('补件原件', { exact: true }).setInputFiles(image);
  await page.getByRole('button', { name: '上传补件原件', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('原件已上传');
  await page.getByLabel('补件字段 业务用途', { exact: true }).fill('日常办公耗材采购，金额与业务事项一致');
  await page.getByLabel('异常处理依据', { exact: true }).fill('已补充清晰票据扫描和采购用途');
  await page.getByRole('button', { name: '提交补件核验', exact: true }).click();
  await expect(page.getByText('已提交 · 待核验', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/stage8-employee-supplement.png', fullPage: true });
  await page.getByLabel('切换验证身份').selectOption('accountant');
  await page.getByRole('button', { name: '异常与补件', exact: true }).click();
  await page.getByRole('button').filter({ hasText: '第八阶段补充合同用途' }).click();
  await page.getByLabel('异常处理操作', { exact: true }).selectOption('accept');
  await page.getByLabel('替代 ' + image.name, { exact: true }).check();
  await page.getByLabel('异常处理依据', { exact: true }).fill('清晰扫描与原票据对应，用途已核实，继续检查财务异常');
  await page.getByRole('button', { name: '接受补件并更新证据版本', exact: true }).click();
  await expect(page.getByText('核验后处理中', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/stage8-accountant-case.png', fullPage: true });
  const updated = await (await request.get(`/api/business/events/${event.id}`, { headers: headers.accountant })).json(); expect(updated.attachments).toHaveLength(2);
  const draft = await (await request.get(`/api/books/${mapping.book_id}/drafts/${mapping.draft_id}`, { headers: headers.accountant })).json(); expect(draft.status).toBe('draft');
  expect(draft.issues.some((issue: string) => issue.includes('异常待办未关闭'))).toBe(true);
  const detail = await (await request.get(`/api/cases/${task.id}`, { headers: headers.employee })).json(); expect(detail.history.every((row: { audience: string }) => row.audience === 'requester')).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
