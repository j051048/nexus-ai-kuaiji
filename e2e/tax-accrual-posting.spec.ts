import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, pinPersona, reviewTaxPaper, taxFullFixture, taxPaper } from './n04-tax-fixtures';

test.setTimeout(150_000);
test.use({ actionTimeout: 15_000 });

// 四税计提默认科目：6403/6801 借方、2221.03/2221.05 贷方。
const ACCRUAL_ACCOUNTS = [
  { code: '6403', name: '虚构税金及附加', category: 'expense', normal_balance: 'debit' },
  { code: '6801', name: '虚构所得税费用', category: 'expense', normal_balance: 'debit' },
  {
    code: '2221.03',
    name: '虚构应交城建税及附加',
    category: 'liability',
    normal_balance: 'credit',
  },
  { code: '2221.05', name: '虚构应交企业所得税', category: 'liability', normal_balance: 'credit' },
];

test('STEP1a 四税计提申请必须由另一人复核后才能过账', async ({ page, request }) => {
  const f = await taxFullFixture(request);
  for (const item of ACCRUAL_ACCOUNTS) {
    await checked(
      await request.post('/api/opening/accounts', {
        headers: f.headers,
        data: {
          ...item,
          ledger_id: f.ledger.id,
          effective_from: '2026-01',
          effective_to: '2026-12',
          reason: '本地虚构四税计提科目',
        },
      }),
    );
  }
  // 档案附加税率 12%：底稿与计提必须用同一个税率，否则服务端拒绝两套依据。
  const paper = await taxPaper(request, f);
  await reviewTaxPaper(request, f, paper);

  await pinPersona(page, 'accountant');
  const panel = await openTax(page, f);
  const accrual = panel.getByRole('region', { name: '四税计提预览', exact: true });
  await expect(accrual).toBeVisible();
  const requestButton = accrual.getByRole('button', {
    name: '生成计提手工凭证申请',
    exact: true,
  });
  // 没有累计利润时不得计提所得税：按钮不可用，且页面给出服务端的真实原因。
  await expect(accrual).toContainText('累计利润');
  await expect(requestButton).toBeDisabled();

  await accrual.getByLabel('本期累计利润（元）', { exact: true }).fill('1000');
  await accrual
    .getByLabel('人工调整原因', { exact: true })
    .fill('本地端到端验证：本年度前期快照不全，按人工核定本年累计利润 1000 元计提');
  const previewed = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/tax/accrual-preview') &&
      response.request().method() === 'POST' &&
      (response.request().postDataJSON() as { cumulative_profit_cents: number | null })
        .cumulative_profit_cents === 100_000,
  );
  await accrual.getByRole('button', { name: '重新计算预览', exact: true }).click();
  expect((await previewed).status()).toBe(200);
  await expect(accrual).toContainText('6403');
  await expect(accrual).toContainText('2221.03');
  // 附加税 30.00×12% = 3.60，所得税 1000.00×25% = 250.00。
  await expect(accrual).toContainText('253.60');
  await expect(requestButton).toBeEnabled();

  const requested = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/tax/accrual-manual-request') &&
      response.request().method() === 'POST',
  );
  await requestButton.click();
  const response = await requested;
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json();
  const short = String(body.request.id).slice(0, 8);
  expect(body.posted).toBe(false);
  expect(body.voucher.debit_cents).toBe(body.voucher.credit_cents);
  expect(body.voucher.line_count).toBe(4);
  expect(body.surtax_basis).toMatchObject({ source: 'tax_profile', rate_bp: 1200 });
  await expect(accrual).toContainText(`已生成申请 #${short}`);
  await expect(accrual).toContainText('待复核');
  await expect(accrual).toContainText('附加税按税务档案核定税率 12% 计征');
  await expect(accrual).toContainText('手工凭证办理');
  await expect(accrual).not.toContainText('已过账');

  // 重复点击只重放同一张申请，不会生成第二条待审请求。
  const replaying = page.waitForResponse(
    (replay) =>
      replay.url().endsWith('/api/tax/accrual-manual-request') &&
      replay.request().method() === 'POST',
  );
  await requestButton.click();
  expect((await (await replaying).json()).request.id).toBe(body.request.id);
  const listed = await checked(
    await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.headers }),
    200,
  );
  // 夹具里已有其他手工凭证，只数本功能生成的计提申请。
  const accrualRequests = listed.requests.filter((item: { content: { command_key: string } }) =>
    String(item.content.command_key).startsWith('tax-accrual:'),
  );
  expect(accrualRequests).toHaveLength(1);
  expect(accrualRequests[0].id).toBe(body.request.id);

  // 另一名有复核权的真实身份批准；界面刷新后状态随之推进，而不是一直显示待复核。
  const approved = await checked(
    await request.post(`/api/posting/manual-requests/${body.request.id}/approve`, {
      headers: f.reviewer,
      data: {
        expected_version: body.request.version,
        expected_policy_version: body.request.policy_version,
        reason: '独立复核四税计提分录与底稿金额一致',
      },
    }),
    200,
  );
  await accrual.getByRole('button', { name: '刷新申请状态', exact: true }).click();
  await expect(accrual).toContainText('已复核，待过账');

  const posted = await checked(
    await request.post(`/api/posting/manual-requests/${body.request.id}/post`, {
      headers: f.headers,
      data: {
        expected_version: approved.request.version,
        command_key: body.voucher.command_key,
      },
    }),
  );
  expect(posted.voucher.review_request_id).toBe(body.request.id);
  await accrual.getByRole('button', { name: '刷新申请状态', exact: true }).click();
  await expect(accrual).toContainText('已过账');
});
