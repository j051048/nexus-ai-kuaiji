import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

test.setTimeout(60_000);

const image = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
  'base64',
);

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

async function bindPersona(page: Page, persona: string) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

async function submittedEvent(
  request: APIRequestContext,
  options: { persona?: string; companyId?: string } = {},
) {
  const employee = await identity(request, options.persona ?? 'employee');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const owner = await identity(request, 'accountant');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find(
    (item) =>
      item.can_submit && (options.companyId ? item.id === options.companyId : !!item.department_id),
  )!;
  expect(company).toBeTruthy();
  const response = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: `分步引导虚构报销-${crypto.randomUUID()}`,
        counterparty: '虚构文具商',
        amount: '38.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '本地流程引导验证',
      },
    },
  });
  expect(response.status()).toBe(201);
  let event: BusinessEvent = await response.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers: employee,
      multipart: { file: { name: '虚构票据.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.status()).toBe(200);
  event = await upload.json();
  const submit = await request.post(`/api/business/events/${event.id}/submit`, {
    headers: employee,
    data: { expected_version: event.version },
  });
  expect(submit.status()).toBe(200);
  event = await submit.json();
  return { employee, department, finance, owner, company, event };
}

test('员工录入引导随填写和原件变化，未确认时不自动提交，手机可读', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  await page
    .getByRole('region', { name: '提交与进度' })
    .getByRole('button', { name: /提交报销/ })
    .click();
  const dialog = page.getByRole('dialog');
  const guide = dialog.getByRole('region', { name: '员工录入引导', exact: true });
  await expect(guide).toBeVisible();
  await expect(guide.locator('[aria-current="step"]')).toHaveCount(1);
  const writes: string[] = [];
  page.on('request', (req) => {
    if (
      req.method() === 'POST' &&
      /\/complete$|\/submit$|\/approve$|\/exports$/.test(new URL(req.url()).pathname)
    )
      writes.push(req.url());
  });
  await dialog.getByLabel('事项金额', { exact: true }).fill('38.20');
  await dialog.getByLabel('事项摘要', { exact: true }).fill('分步引导待提交草稿');
  await dialog.getByLabel('交易对方', { exact: true }).fill('虚构文具商');
  await dialog.getByLabel('发生日期', { exact: true }).fill('2026-10-12');
  await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
  await expect(guide).toContainText('下一步');
  await dialog.getByLabel('一体化录入上传附件', { exact: true }).setInputFiles({
    name: '分步引导虚构票据.png',
    mimeType: 'image/png',
    buffer: image,
  });
  await expect(guide).toContainText('待核对确认');
  await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
  await dialog
    .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
    .click();
  await expect(guide).toContainText('可以提交，尚未提交');
  await guide.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/role3-intake-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 960 });
  await guide.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/role3-intake-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(writes).toEqual([]);
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await dialog.getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('region', { name: '事项办理引导' })).toContainText('等待部门审批');
  expect(writes).toHaveLength(1);
  expect(new URL(writes[0]).pathname).toMatch(/\/complete$/);
});

test('财务核验和交接各有下一步，交接不等于凭证审核或导出', async ({ page, request }) => {
  const fixture = await submittedEvent(request);
  const approval = await request.post(
    `/api/business/events/${fixture.event.id}/department-approve`,
    {
      headers: fixture.department,
      data: { expected_version: fixture.event.version },
    },
  );
  expect(approval.status()).toBe(200);
  await bindPersona(page, 'finance');
  await page.goto(`/workspace/business?event=${fixture.event.id}`, {
    waitUntil: 'domcontentloaded',
  });
  const guide = page.getByRole('region', { name: '事项办理引导', exact: true });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText('核验');
  await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
  await expect(guide).toContainText('交接');
  const bookResponse = await request.post('/api/books', {
    headers: fixture.owner,
    data: {
      name: `引导交接账套-${crypto.randomUUID()}`,
      company_id: fixture.company.id,
      period: '2026-10',
    },
  });
  expect(bookResponse.status()).toBe(201);
  const targetBook = await bookResponse.json();
  expect(
    (
      await request.put(`/api/books/${targetBook.id}/members/local-business-finance`, {
        headers: fixture.owner,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  // The real company-scoped selector re-reads allowed books on a fresh selection.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByLabel('交接到账套', { exact: true }).selectOption(targetBook.id);
  await page.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
  await expect(guide).toContainText('业务已交接会计');
  const current: BusinessEvent = await (
    await request.get(`/api/business/events/${fixture.event.id}`, { headers: fixture.finance })
  ).json();
  expect(current.status).toBe('handed_off');
  const detail = await (
    await request.get(`/api/books/${current.mapping!.book_id}`, { headers: fixture.owner })
  ).json();
  expect(detail.drafts.every((draft: { status: string }) => draft.status === 'draft')).toBe(true);
  expect(
    (
      await (
        await request.get(`/api/books/${current.mapping!.book_id}/exports`, {
          headers: fixture.owner,
        })
      ).json()
    ).exports,
  ).toEqual([]);
  await guide.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/role3-finance-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await guide.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/role3-finance-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('原件无法核验时会计引导解释阻断，不会因为打开引导就自动审核', async ({ page, request }) => {
  const owner = await identity(request, 'accountant');
  const book = await (
    await request.post('/api/books', {
      headers: owner,
      data: { name: `引导复核-${crypto.randomUUID()}`, period: '2026-10' },
    })
  ).json();
  const name = `原件待核验-${crypto.randomUUID()}`;
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers: owner,
        data: {
          source_name: '虚构来源',
          rows: [
            {
              external_id: crypto.randomUUID(),
              date: '2026-10-12',
              description: name,
              counterparty: '虚构文具商',
              amount: '38.20',
              direction: 'expense',
              evidence_ref: '仅合成文字引用，没有已核验原件',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (await request.post(`/api/books/${book.id}/suggestions`, { headers: owner })).status(),
  ).toBe(200);
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('筛选待办类型', { exact: true }).selectOption('review');
  const row = page.locator('.workspace-task').filter({ hasText: name });
  await expect(row).toBeVisible();
  const writes: string[] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/approve$|\/exports$/.test(new URL(req.url()).pathname))
      writes.push(req.url());
  });
  await row.getByRole('button', { name: '同屏复核', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  const guide = dialog.getByRole('region', { name: '会计复核引导', exact: true });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText('原件');
  await expect(dialog.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  expect(writes).toEqual([]);
  await page.screenshot({ path: 'tmp_test/role3-review-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await guide.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/role3-review-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('退回事项展示真实原因并回到同一事项修改，员工不能代财务审批', async ({ page, request }) => {
  const fixture = await submittedEvent(request);
  const reason = '请补充本次采购的业务用途（虚构引导测试）';
  const response = await request.post(`/api/business/events/${fixture.event.id}/return`, {
    headers: fixture.department,
    data: { expected_version: fixture.event.version, reason },
  });
  expect(response.status()).toBe(200);
  await bindPersona(page, 'employee');
  await page.goto(`/workspace/business?event=${fixture.event.id}`, {
    waitUntil: 'domcontentloaded',
  });
  const guide = page.getByRole('region', { name: '本次退回与补充说明' });
  await expect(guide).toContainText(reason);
  await expect(guide).toContainText('修改并重新提交');
  await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toHaveCount(0);
  await guide.getByRole('button', { name: '修改并重新提交', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '修改业务事项', exact: true });
  await expect(dialog.getByRole('region', { name: '员工录入引导' })).toContainText('尚未提交');
  await expect(dialog.getByLabel('事项摘要', { exact: true })).toHaveValue(
    fixture.event.payload.description,
  );
});

test('直提资料按实际模式等待会计接收，不虚构部门或财务审批', async ({ page, request }) => {
  const owner = await identity(request, 'accountant');
  const applicant = await identity(request, 'finance');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: applicant })
  ).json();
  const company = context.companies.find((item) => item.can_submit && item.can_finance)!;
  expect(company).toBeTruthy();
  const path = `/api/companies/${company.id}/submission-policy`;
  const policy = await (await request.get(path, { headers: owner })).json();
  expect(
    (
      await request.put(path, {
        headers: owner,
        data: {
          mode: 'direct',
          expected_version: policy.version,
          reason: '本地合成引导直提路径验证',
        },
      })
    ).status(),
  ).toBe(200);
  try {
    const fixture = await submittedEvent(request, { persona: 'finance', companyId: company.id });
    expect(fixture.event.submission_mode).toBe('direct');
    await bindPersona(page, 'finance');
    await page.goto(`/workspace/business?event=${fixture.event.id}`, {
      waitUntil: 'domcontentloaded',
    });
    const guide = page.getByRole('region', { name: '事项办理引导' });
    await expect(guide).toContainText('等待授权会计接收');
    await expect(guide).not.toContainText('等待部门审批');
    await expect(guide.locator('ol')).not.toContainText('财务核验');
    await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toHaveCount(0);
    expect(fixture.event.status).toBe('submitted');
  } finally {
    const current = await (await request.get(path, { headers: owner })).json();
    expect(
      (
        await request.put(path, {
          headers: owner,
          data: {
            mode: policy.mode,
            expected_version: current.version,
            reason: '恢复本机合成企业原有提交方式',
          },
        })
      ).status(),
    ).toBe(200);
  }
});

test('常规复核失败后引导要求重新读取，不把冲突当审核成功或自动重发', async ({ page, request }) => {
  const owner = await identity(request, 'accountant');
  const preparer = await identity(request, 'finance');
  const bookResponse = await request.post('/api/books', {
    headers: owner,
    data: {
      name: `复核故障引导-${crypto.randomUUID()}`,
      period: '2026-10',
    },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  expect(
    (
      await request.put(`/api/companies/${book.company_id}/members`, {
        headers: owner,
        data: { email: 'finance@demo.example.invalid', role: 'finance', department_id: null },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: owner,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers: preparer,
        data: {
          source_name: '本地故障注入',
          rows: [
            {
              external_id: crypto.randomUUID(),
              date: '2026-10-12',
              description: '办公用品复核故障虚构测试',
              counterparty: '虚构文具商',
              amount: '38.20',
              direction: 'expense',
              evidence_ref: '仅本地合成引用，不表示真实原件已核验',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (await request.post(`/api/books/${book.id}/suggestions`, { headers: preparer })).status(),
  ).toBe(200);
  const draft = (await (await request.get(`/api/books/${book.id}`, { headers: owner })).json())
    .drafts[0];
  expect(draft.review_responsibility.maker_user_id).toBe('local-business-finance');
  expect(draft.review_responsibility.can_review).toBe(true);
  let sends = 0;
  await page.route(`**/api/books/${book.id}/drafts/${draft.id}/approve`, async (route) => {
    sends++;
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '本地故障注入：凭证版本已变化' }),
    });
  });
  await page.goto(`/workspace/accounting?book=${book.id}&draft=${draft.id}`, {
    waitUntil: 'domcontentloaded',
  });
  const guide = page.getByRole('region', { name: '会计复核引导' });
  await expect(guide).toBeVisible();
  await page.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: '本地故障注入：凭证版本已变化' }),
  ).toBeVisible();
  await expect(guide).toContainText('本次操作未确认完成');
  await expect(guide).toContainText('重新读取');
  expect(sends).toBe(1);
  const current = await (
    await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers: owner })
  ).json();
  expect(current.status).toBe('draft');
});
