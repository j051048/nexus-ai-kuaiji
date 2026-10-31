import { expect, test, type APIRequestContext, type APIResponse } from '@playwright/test';

test.setTimeout(120_000);
test.use({ actionTimeout: 20_000 });

async function checked(response: APIResponse, expected = 201) {
  expect(response.status(), await response.text()).toBe(expected);
  return response.json();
}

async function identity(request: APIRequestContext, persona: string) {
  const session = await checked(await request.get(`/api/session?persona=${persona}`), 200);
  return { Authorization: `Bearer ${session.token}` };
}

/** Three real steps: prepare, independent review, post. No fixture bypass. */
async function reviewedManual(
  request: APIRequestContext,
  preparer: { Authorization: string },
  reviewer: { Authorization: string },
  data: Record<string, unknown>,
) {
  const prepared = await checked(
    await request.post('/api/posting/manual-requests', { headers: preparer, data }),
  );
  const approved = await checked(
    await request.post(`/api/posting/manual-requests/${prepared.request.id}/approve`, {
      headers: reviewer,
      data: {
        expected_version: prepared.request.version,
        expected_policy_version: prepared.request.policy_version,
        reason: '真实本地独立身份逐项核对手工凭证',
      },
    }),
    200,
  );
  return checked(
    await request.post(`/api/posting/manual-requests/${prepared.request.id}/post`, {
      headers: preparer,
      data: { expected_version: approved.request.version, command_key: data.command_key },
    }),
  );
}

/** A real blocked close: the month has a bank and a payable balance but no
 * control-account configuration and no bank coverage, so both subledger
 * checks must block with server-declared destinations. */
async function blockedCloseFixture(request: APIRequestContext) {
  const headers = await identity(request, 'accountant');
  const finance = await identity(request, 'finance');
  const tag = `阻断直达虚构-${crypto.randomUUID().slice(0, 8)}`;
  const company = await checked(
    await request.post('/api/companies', { headers, data: { name: tag, tax_id: '' } }),
  );
  const book = await checked(
    await request.post('/api/books', {
      headers,
      data: { company_id: company.id, name: `${tag}-2026-10`, period: '2026-10' },
    }),
  );
  await checked(
    await request.put(`/api/companies/${company.id}/members`, {
      headers,
      data: { email: 'finance@demo.example.invalid', role: 'finance', department_id: null },
    }),
    200,
  );
  // The local harness seeds one firm owner only; DCP01 needs an independent
  // reviewer identity that is allowed to hold book review responsibility.
  await checked(
    await request.put('/api/firm/members', {
      headers,
      data: { email: 'finance@demo.example.invalid', role: 'reviewer' },
    }),
    200,
  );
  await checked(
    await request.put(`/api/books/${book.id}/members/local-business-finance`, {
      headers,
      data: { role: 'reviewer' },
    }),
    200,
  );
  await checked(
    await request.post(`/api/books/${book.id}/ledger/managers`, {
      headers,
      data: { user_id: 'local-business-finance', reason: '虚构场景独立核准正式期初' },
    }),
  );
  const ledger = await checked(await request.get(`/api/books/${book.id}/ledger`, { headers }), 200);
  const policy = await checked(
    await request.post('/api/opening/policies', {
      headers,
      data: {
        ledger_id: ledger.id,
        standard_kind: 'small_enterprise',
        taxpayer_kind: 'general',
        effective_from: '2026-01',
        effective_to: '2026-12',
        reason: '仅本地虚构月结阻断直达验收',
      },
    }),
  );
  const accounts: { id: string }[] = [];
  for (const item of [
    { code: '1002', name: '虚构银行存款', category: 'asset', normal_balance: 'debit' },
    { code: '2202', name: '虚构应付账款', category: 'liability', normal_balance: 'credit' },
  ])
    accounts.push(
      await checked(
        await request.post('/api/opening/accounts', {
          headers,
          data: {
            ...item,
            ledger_id: ledger.id,
            effective_from: '2026-01',
            effective_to: '2026-12',
            reason: '虚构账簿科目',
          },
        }),
      ),
    );
  const opening = await checked(
    await request.post('/api/opening/packages', {
      headers,
      data: { ledger_id: ledger.id, policy_id: policy.id, target_period: '2026-10' },
    }),
  );
  const version = await checked(
    await request.post(`/api/opening/packages/${opening.package.id}/versions`, {
      headers,
      data: {
        idempotency_key: `opening-${crypto.randomUUID()}`,
        reason: '虚构零业务期初，用独立身份审核',
        lines: accounts.map((account) => ({
          account_id: account.id,
          account_version: 1,
          year_opening: '0.00',
          ytd_debit: '0.00',
          ytd_credit: '0.00',
          opening: '0.00',
        })),
      },
    }),
  );
  const versionId = version.version.id;
  await checked(
    await request.post(`/api/opening/versions/${versionId}/validate`, { headers }),
    200,
  );
  const command = { expected_version: 1, reason: '虚构正式期初独立核准与冻结' };
  await checked(
    await request.post(`/api/opening/versions/${versionId}/submit`, { headers, data: command }),
    200,
  );
  await checked(
    await request.post(`/api/opening/versions/${versionId}/approve`, {
      headers: finance,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post(`/api/opening/versions/${versionId}/freeze`, {
      headers: finance,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post('/api/posting/opening-bindings', {
      headers,
      data: { book_id: book.id, package_version_id: versionId, reason: '本地绑定冻结期初' },
    }),
  );
  // One posted voucher gives both subledger checks real amounts to inspect:
  // a bank balance without coverage and a payable balance without control
  // configuration. Zero-activity months pass these checks vacuously.
  await reviewedManual(request, finance, headers, {
    book_id: book.id,
    command_key: `dcp01-${crypto.randomUUID()}`,
    voucher_date: '2026-10-08',
    description: '虚构一笔银行付出与应付余额，使子账与银行覆盖检查有发生额',
    reason: '本地虚构月结阻断直达验收',
    lines: [
      { account_id: accounts[0].id, account_version: 1, debit: '100.00', credit: '0.00' },
      { account_id: accounts[1].id, account_version: 1, debit: '0.00', credit: '100.00' },
    ],
  });
  const report = await checked(
    await request.post('/api/reports', {
      headers: finance,
      data: { book_id: book.id, period: '2026-10' },
    }),
  );
  await checked(await request.post(`/api/reports/${report.report.id}/validate`, { headers: finance }), 200);
  await checked(
    await request.post(`/api/reports/${report.report.id}/review`, {
      headers,
      data: { expected_version: 1, reason: '事务所复核人独立复核客户财务编制的报表' },
    }),
    200,
  );
  const close = await checked(
    await request.post('/api/b04-close/preflight', {
      headers,
      data: {
        book_id: book.id,
        period: '2026-10',
        report_id: report.report.id,
        idempotency_key: `close-${crypto.randomUUID()}`,
        reason: '真实接口本地虚构阻断直达验收',
      },
    }),
  );
  expect(close.close.status, JSON.stringify(close.checks)).toBe('preflight_failed');
  const control = close.checks.find(
    (check: { code: string }) => check.code === 'subledger_control',
  );
  expect(control.status).toBe('blocked');
  expect(control.action_target).toEqual({
    kind: 'subledger',
    book_id: book.id,
    period: '2026-10',
  });
  const bank = close.checks.find((check: { code: string }) => check.code === 'bank_reconciliation');
  expect(bank.status).toBe('blocked');
  expect(bank.action_target).toEqual({
    kind: 'bank_reconciliation',
    book_id: book.id,
    period: '2026-10',
  });
  return { company, book };
}

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`月结阻断直达办理并在返回后保留账套与月份：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await blockedCloseFixture(request);
    await page.setViewportSize(viewport);
    const path = `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`;
    await page.goto(path);
    await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
    await page.getByLabel('办理说明（至少 3 字）').fill('本地虚构阻断直达验收');
    await page.getByLabel('本次报表版本').selectOption({ index: 1 });
    await page.getByRole('button', { name: '生成月结预检', exact: true }).click();
    const action = page.getByRole('button', { name: '去补银行账户与覆盖', exact: true });
    const controlAction = page.getByRole('button', { name: '去配置控制科目', exact: true });
    await expect(action).toBeVisible({ timeout: 30_000 });
    await expect(controlAction).toBeVisible();
    await controlAction.click();
    const setup = page.locator('.formal-close-setup');
    await expect(setup).toBeVisible();
    await expect(setup.getByRole('heading', { name: '控制科目配置', exact: true })).toBeVisible();
    await action.click();
    await expect(setup.getByRole('heading', { name: '银行账户与覆盖证据', exact: true })).toBeVisible();
    await expect(setup.getByText('正在读取当前配置…', { exact: false })).toHaveCount(0, {
      timeout: 30_000,
    });
    await expect(setup.getByRole('heading', { name: '建立银行账户映射', exact: true })).toBeVisible();
    // Configuration is never a substitute for evidence: the panel says so.
    await expect(setup).toContainText('配置本身不会通过检查');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`close-action-${viewport.name}.png`),
      fullPage: true,
    });
    // Returning keeps the same book and period instead of dropping the scope.
    await page.goto(path);
    await expect(page.locator('.formal-books-workspace')).toContainText('2026-10');
  });
}
