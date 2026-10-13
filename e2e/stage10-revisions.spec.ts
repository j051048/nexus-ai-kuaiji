import { expect, test } from '@playwright/test';

test('事实修订真实 UI：财务提议、另一会计独立复核、旧证据保留与新草稿待审', async ({ page, request }) => {
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['employee', 'department', 'finance', 'accountant']) {
    const response = await request.get(`/api/session?persona=${persona}`); expect(response.status()).toBe(200);
    headers[persona] = { Authorization: `Bearer ${(await response.json()).token}` };
  }
  const marker = crypto.randomUUID().slice(0, 8), description = `第十阶段事实纠错合成办公费 ${marker}`;
  const company = (await (await request.get('/api/business/context', { headers: headers.employee })).json()).companies[0];
  let response = await request.post('/api/business/events', { headers: headers.employee, data: {
    company_id: company.id, template_id: 'reimbursement', payload: { occurred_on: '2026-10-03', description, counterparty: '明确虚构的办公商店', amount: '100.00', direction: 'expense', payment_status: 'unpaid', category: 'office', purpose: '明确合成的金额纠错验证' },
  } });
  expect(response.status()).toBe(201); let event = await response.json();
  const file = { name: `合成纠错票据-${marker}.png`, mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') };
  response = await request.post(`/api/business/events/${event.id}/attachments?expected_version=${event.version}`, { headers: headers.employee, multipart: { file } });
  expect(response.status()).toBe(200); event = await response.json();
  for (const [action, persona] of [['submit', 'employee'], ['department-approve', 'department'], ['finance-approve', 'finance']]) {
    response = await request.post(`/api/business/events/${event.id}/${action}`, { headers: headers[persona], data: { expected_version: event.version } });
    expect(response.status()).toBe(200); event = await response.json();
  }
  const book = (await (await request.get('/api/workbench', { headers: headers.finance })).json()).books.find((value: { company_id: string }) => value.company_id === company.id);
  response = await request.post(`/api/business/events/${event.id}/handoff`, { headers: headers.finance, data: { expected_version: event.version, book_id: book.id, idempotency_key: crypto.randomUUID() } });
  expect(response.status()).toBe(200); const original = await response.json();
  await page.goto('/'); await page.getByLabel('切换验证身份').selectOption('finance');
  await page.getByRole('button', { name: '事实修订与调整', exact: true }).click();
  await expect(page.getByRole('heading', { name: '业务事实纠错', exact: true })).toBeVisible();
  await page.getByLabel('待修订事项编号', { exact: true }).fill(event.id);
  await page.getByRole('button', { name: '读取原事实', exact: true }).click();
  await expect(page.getByLabel('修订金额', { exact: true })).toHaveValue('100.00');
  await page.getByLabel('修订金额', { exact: true }).fill('80.00');
  await page.getByLabel('事实修订依据', { exact: true }).fill('原票据为80元，合成申请误填100元，现提交另一位会计核对');
  const submission = page.waitForResponse(r => new URL(r.url()).pathname === `/api/fact-revisions/events/${event.id}` && r.request().method() === 'POST');
  await page.getByRole('button', { name: '提交独立复核', exact: true }).click();
  const submitted = await submission; expect(submitted.status()).toBe(200); const revision = await submitted.json();
  expect(revision.state).toBe('pending');
  await expect(page.getByRole('button', { name: '核验并接受修订', exact: true })).toHaveCount(0);
  const before = await (await request.get(`/api/books/${book.id}/drafts/${original.draft_id}`, { headers: headers.accountant })).json();
  expect(before.status).toBe('draft'); expect(before.source_amount).toBe('100.00');
  await page.getByLabel('切换验证身份').selectOption('accountant');
  await page.getByRole('button', { name: '事实修订与调整', exact: true }).click();
  await page.locator('.revision-item').filter({ hasText: description }).click();
  await expect(page.locator('.revision-diff')).toContainText('100.00');
  await expect(page.locator('.revision-diff')).toContainText('80.00');
  await page.getByLabel('独立复核依据', { exact: true }).fill('已独立核对合成原票据和业务说明，接受金额80元的事实修订');
  const approval = page.waitForResponse(r => new URL(r.url()).pathname === `/api/fact-revisions/${revision.id}/review` && r.request().method() === 'POST');
  await page.getByRole('button', { name: '核验并接受修订', exact: true }).click();
  const reviewed = await approval; expect(reviewed.status()).toBe(200); const result = await reviewed.json();
  expect(result.state).toBe('replaced'); expect(result.lineage.old_draft_id).toBe(original.draft_id);
  expect(result.lineage.new_draft_id).not.toBe(original.draft_id);
  const current = await (await request.get(`/api/books/${book.id}/drafts/${result.lineage.new_draft_id}`, { headers: headers.accountant })).json();
  expect(current.status).toBe('draft'); expect(current.source_amount).toBe('80.00');
  const historical = await (await request.get(`/api/books/${book.id}/sources/${original.source_id}/history`, { headers: headers.accountant })).json();
  expect(historical.payload.amount).toBe('100.00'); expect(historical.submitted_snapshot.payload.amount).toBe('100.00');
  expect(historical.attachments[0].id).toBe(event.attachments[0].id); expect(JSON.stringify(historical)).not.toContain('storage_key');
  expect((await request.get(`/api/fact-revisions/${revision.id}`, { headers: headers.employee })).status()).toBe(404);
  await page.screenshot({ path: 'artifacts/stage10-independent-fact-revision.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
