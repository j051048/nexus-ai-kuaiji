import { expect, test } from '@playwright/test';

const receipt = { name: '第七阶段跨页虚构票据.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC', 'base64') };

test('业务移交和会计复核使用独立授权账套列表，可打开工作台第 20 项之后的账套', async ({ page, request }) => {
  test.setTimeout(120_000);
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['accountant', 'employee', 'department', 'finance']) {
    const response = await request.get(`/api/session?persona=${persona}`);
    expect(response.status()).toBe(200);
    headers[persona] = { Authorization: `Bearer ${(await response.json()).token}` };
  }
  const companyResponse = await request.get('/api/companies', { headers: headers.accountant });
  const company = (await companyResponse.json()).companies.find((item: { name: string }) => item.name === '青屿设计（虚构示例）');
  expect(company).toBeTruthy();
  const tag = Date.now().toString();
  let target: { id: string; name: string } | null = null;
  // These real authorized books force the destination off both accounting and
  // company-finance workbench pages. All data remains in the disposable E2E DB.
  for (let index = 0; index < 22; index += 1) {
    const name = index === 21 ? `ZZZ-第七阶段跨页目标-${tag}` : `000-第七阶段跨页占位-${tag}-${String(index).padStart(2, '0')}`;
    const response = await request.post('/api/books', { headers: headers.accountant, data: { name, period: '2026-10', company_id: company.id } });
    expect(response.status()).toBe(201);
    const book = await response.json();
    const grant = await request.put(`/api/books/${book.id}/members/local-business-finance`, { headers: headers.accountant, data: { role: 'editor' } });
    expect(grant.status()).toBe(200);
    if (index === 21) target = book;
  }
  expect(target).toBeTruthy();
  const firstPageResponse = await request.get('/api/workbench?limit=20&offset=0', { headers: headers.finance });
  const firstPage = await firstPageResponse.json();
  expect(firstPage.books).toHaveLength(20);
  expect(firstPage.books.some((book: { id: string }) => book.id === target!.id)).toBe(false);

  const description = `第七阶段跨页移交-${tag}`;
  let response = await request.post('/api/business/events', { headers: headers.employee, data: {
    company_id: company.id, template_id: 'reimbursement', payload: {
      occurred_on: '2026-10-10', description, counterparty: '虚构票据供应商', amount: '268.10',
      direction: 'expense', payment_status: 'unpaid', category: 'office', purpose: '独立账套分页验证',
    },
  } });
  expect(response.status()).toBe(201);
  let event = await response.json();
  response = await request.post(`/api/business/events/${event.id}/attachments?expected_version=${event.version}`, { headers: headers.employee, multipart: { file: receipt } });
  expect(response.status()).toBe(200); event = await response.json();
  for (const [persona, action] of [['employee', 'submit'], ['department', 'department-approve'], ['finance', 'finance-approve']]) {
    response = await request.post(`/api/business/events/${event.id}/${action}`, { headers: headers[persona], data: { expected_version: event.version } });
    expect(response.status()).toBe(200); event = await response.json();
  }

  await page.goto('/');
  await page.getByLabel('切换验证身份').selectOption('finance');
  await page.getByRole('button', { name: '业务事项', exact: true }).click();
  await page.getByRole('tab', { name: '企业财务', exact: true }).click(); if (await page.getByLabel('筛选业务事项状态', { exact: true }).inputValue() !== '') await page.getByLabel('筛选业务事项状态', { exact: true }).selectOption('');
  await page.getByRole('button').filter({ has: page.getByText(description, { exact: true }) }).click();
  await expect(page.locator('#handoff-book option').filter({ hasText: target!.name })).toHaveCount(1);
  await page.getByLabel('交接到账套', { exact: true }).selectOption(target!.id);
  await page.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已生成凭证草稿');
  await expect(page.getByRole('button', { name: '打开会计复核', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '打开会计复核', exact: true }).click();
  await expect(page.getByLabel('凭证摘要', { exact: true })).toHaveValue(description);
  await expect(page.getByRole('heading', { name: target!.name, exact: true })).toBeVisible();

  await page.getByLabel('切换验证身份').selectOption('accountant');
  await page.getByRole('button', { name: '业务事项', exact: true }).click();
  await page.getByRole('tab', { name: '会计接收', exact: true }).click();
  // The business company filter is built from current authorization context,
  // including a destination company absent from the accounting workbench page.
  await expect(page.getByLabel('筛选业务事项企业').locator('option').filter({ hasText: company.name })).toHaveCount(1);
  await page.getByLabel('筛选业务事项企业').selectOption(company.id);
  await page.getByRole('button').filter({ has: page.getByText(description, { exact: true }) }).click();
  await page.getByRole('button', { name: '打开会计复核', exact: true }).click();
  await expect(page.getByLabel('凭证摘要', { exact: true })).toHaveValue(description);
  await expect(page.getByRole('heading', { name: target!.name, exact: true })).toBeVisible();
});
