import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);
test.use({ actionTimeout: 15_000 });

async function checked(response: APIResponse, expected = 201) {
  expect(response.status(), await response.text()).toBe(expected);
  return response.json();
}

/** Use the public responsibility flow; no fixture bypass of independent review. */
async function reviewedManual(
  request: APIRequestContext,
  preparer: { Authorization: string },
  reviewer: { Authorization: string },
  data: Record<string, unknown>,
) {
  const prepared = await checked(
    await request.post('/api/posting/manual-requests', {
      headers: preparer,
      data,
    }),
  );
  expect(prepared.request.prepared_by).not.toBe('local-reviewer');
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
  expect(approved.request.independent).toBe(true);
  return checked(
    await request.post(`/api/posting/manual-requests/${prepared.request.id}/post`, {
      headers: preparer,
      data: { expected_version: approved.request.version, command_key: data.command_key },
    }),
  );
}

async function expandBookSwitcher(page: Page) {
  const summary = page.locator('summary').filter({ hasText: /^切换账套/ });
  if (await summary.count()) {
    if ((await summary.locator('..').getAttribute('open')) === null) await summary.click();
  }
}

async function openManualVoucher(page: Page, bookName: string) {
  // The authorization/detail read replaces an initially unbound workspace.
  // Act only after the requested book identity is actually displayed; clicking
  // the provisional details node loses the toggle when that node is replaced.
  await expect(page.locator('.formal-books-heading')).toContainText(bookName);
  await page.getByText('手工凭证办理（按制单、复核、记账三步办理）', { exact: true }).click();
  await expect(page.getByRole('region', { name: '手工凭证办理', exact: true })).toBeVisible();
}

test('正式账簿 A 迟到响应经 B 返回 A 后不能覆盖新事实', async ({ page, request }, testInfo) => {
  const group = `迟到虚构账簿-${crypto.randomUUID().slice(0, 8)}`;
  const first = await formalFixture(request, false, group);
  const second = await formalFixture(request, false, group);
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  let hold = true;
  let captured = 0;
  let completed = 0;
  const delayedErrors: string[] = [];
  await page.route(`**/api/books/${first.book.id}/trial-balance`, async (route) => {
    if (!hold) return route.continue();
    const original = await route.fetch();
    expect(original.ok()).toBe(true);
    captured++;
    await delayed;
    try {
      await route.fulfill({ response: original });
    } catch (failure) {
      // A scope switch is allowed to abort its old transport. It must never render the response.
      delayedErrors.push(failure instanceof Error ? failure.message : String(failure));
    } finally {
      completed++;
    }
  });
  try {
    await page.goto(
      `/workspace/formal-books?book=${first.book.id}&company=${first.company.id}&period=2026-10&search=${encodeURIComponent(group)}`,
    );
    await expect.poll(() => captured, { timeout: 30_000 }).toBeGreaterThan(0);
    await expandBookSwitcher(page);
    await page.getByRole('listitem').filter({ hasText: second.tag }).click();
    const workspace = page.locator('.formal-books-workspace');
    await expect(workspace).toContainText(second.tag);
    await expect(workspace.getByText('10,100.00', { exact: true }).first()).toBeVisible();
    await reviewedManual(request, first.headers, first.reviewer, {
      book_id: first.book.id,
      command_key: `late-${crypto.randomUUID()}`,
      voucher_date: '2026-10-16',
      description: '虚构 A 期间产生的新正式事实',
      reason: '本地晚到响应不得覆盖新事实测试',
      lines: first.accounts.map((account, index) => ({
        account_id: account.id,
        account_version: 1,
        debit: index === 0 ? '50.00' : '0.00',
        credit: index === 1 ? '50.00' : '0.00',
      })),
    });
    hold = false;
    await expandBookSwitcher(page);
    await page.getByRole('listitem').filter({ hasText: first.tag }).click();
    await expect(workspace).toContainText(first.tag);
    await expect(workspace.getByText('10,150.00', { exact: true }).first()).toBeVisible();
    release();
    await expect.poll(() => completed).toBe(captured);
    await expect(workspace.getByText('10,150.00', { exact: true }).first()).toBeVisible();
    await expect(workspace.getByText('10,100.00', { exact: true })).toHaveCount(0);
    // The result is about stale isolation; an abort is recorded separately from acceptance.
    await testInfo.attach('late-response-transport', {
      body: JSON.stringify({ captured, completed, cancelledTransports: delayedErrors.length }),
      contentType: 'application/json',
    });
    await page.screenshot({
      path: testInfo.outputPath('formal-books-late-scope.png'),
      fullPage: true,
    });
  } finally {
    release();
  }
});

async function identity(request: APIRequestContext, persona: string) {
  const session = await checked(await request.get(`/api/session?persona=${persona}`), 200);
  return { Authorization: `Bearer ${session.token}` };
}

/** Everything is created through the real local API; opening approval is a different identity. */
async function formalFixture(
  request: APIRequestContext,
  zeroActivity = false,
  namePrefix = '正式账簿虚构',
) {
  const headers = await identity(request, 'accountant');
  const finance = await identity(request, 'finance');
  const reviewer = await identity(request, 'reviewer');
  const employee = await identity(request, 'employee');
  const tag = `${namePrefix}-${crypto.randomUUID().slice(0, 8)}`;
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
  await checked(
    await request.put(`/api/books/${book.id}/members/local-reviewer`, {
      headers,
      data: { role: 'reviewer' },
    }),
    200,
  );
  await checked(
    await request.post(`/api/books/${book.id}/ledger/managers`, {
      headers,
      data: { user_id: 'local-reviewer', reason: '虚构场景独立核准正式期初' },
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
        reason: '仅本地虚构正式账簿与月度交付验收',
      },
    }),
  );
  const accounts: { id: string; code: string; name: string }[] = [];
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
  const packageResponse = await checked(
    await request.post('/api/opening/packages', {
      headers,
      data: { ledger_id: ledger.id, policy_id: policy.id, target_period: '2026-10' },
    }),
  );
  const version = await checked(
    await request.post(`/api/opening/packages/${packageResponse.package.id}/versions`, {
      headers,
      data: {
        idempotency_key: `opening-${crypto.randomUUID()}`,
        reason: '虚构期初金额，用独立身份审核',
        lines: accounts.map((account, index) => ({
          account_id: account.id,
          account_version: 1,
          year_opening: '0.00',
          ytd_debit: !zeroActivity && index === 0 ? '10000.00' : '0.00',
          ytd_credit: !zeroActivity && index === 1 ? '10000.00' : '0.00',
          opening: zeroActivity ? '0.00' : index === 0 ? '10000.00' : '-10000.00',
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
      headers: reviewer,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post(`/api/opening/versions/${versionId}/freeze`, {
      headers: reviewer,
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
  const voucher = zeroActivity
    ? null
    : await reviewedManual(request, headers, reviewer, {
        book_id: book.id,
        command_key: `manual-${crypto.randomUUID()}`,
        voucher_date: '2026-10-15',
        description: `${tag}-真实接口正式凭证`,
        reason: '虚构资料会计复核后正式记账',
        lines: accounts.map((account, index) => ({
          account_id: account.id,
          account_version: 1,
          debit: index === 0 ? '100.00' : '0.00',
          credit: index === 1 ? '100.00' : '0.00',
        })),
      });
  return { headers, finance, reviewer, employee, tag, company, book, accounts, voucher };
}

/** Legitimate no-activity month: no invented bank statement, receipt, approval or period lock. */
async function monthlyFixture(request: APIRequestContext) {
  const f = await formalFixture(request, true);
  await checked(
    await request.put(`/api/books/${f.book.id}/service-plan`, {
      headers: f.headers,
      data: {
        expected_version: 0,
        lead_user_id: 'local-accountant',
        reviewer_user_id: 'local-reviewer',
        contact_user_id: 'local-business-finance',
        due_on: '2026-11-08',
        notes: '虚构企业本月无业务，空资料清单必须在交付包明确列示',
        reason: '本地验收明确本月负责人、独立复核人与客户接收人',
      },
    }),
    200,
  );
  await checked(
    await request.post('/api/subledger/control-applicability', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        status: 'not_applicable',
        effective_from: '2026-10',
        effective_to: '2026-10',
        reason: '虚构企业本月无往来余额、无历史事项与控制科目配置',
      },
    }),
  );
  const report = await checked(
    await request.post('/api/reports', {
      headers: f.headers,
      data: { book_id: f.book.id, period: '2026-10' },
    }),
  );
  await checked(
    await request.post(`/api/reports/${report.report.id}/validate`, { headers: f.headers }),
    200,
  );
  const command = { expected_version: 1, reason: '本地虚构无业务月结独立复核' };
  await checked(
    await request.post(`/api/reports/${report.report.id}/review`, {
      headers: f.reviewer,
      data: command,
    }),
    200,
  );
  const close = await checked(
    await request.post('/api/b04-close/preflight', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        period: '2026-10',
        report_id: report.report.id,
        idempotency_key: `close-${crypto.randomUUID()}`,
        reason: '真实接口本地虚构零业务月结预检',
      },
    }),
  );
  expect(close.close.status, JSON.stringify(close.checks)).toBe('preflight_passed');
  await checked(
    await request.post(`/api/b04-close/${close.close.id}/review`, {
      headers: f.reviewer,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post(`/api/b04-close/${close.close.id}/lock`, {
      headers: f.headers,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post(`/api/reports/${report.report.id}/publish`, {
      headers: f.headers,
      data: command,
    }),
    200,
  );
  // Receiving client keeps only a viewer grant after independent accounting review.
  await checked(
    await request.put(`/api/books/${f.book.id}/members/local-business-finance`, {
      headers: f.headers,
      data: { role: 'viewer' },
    }),
    200,
  );
  return { ...f, report };
}

for (const viewport of [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`待审手工凭证只由制单本人撤回并保留责任历史：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await formalFixture(request, true);
    const content = {
      book_id: f.book.id,
      command_key: `withdraw-${crypto.randomUUID()}`,
      voucher_date: '2026-10-20',
      description: `${f.tag}-待撤回`,
      reason: '虚构数据准备后发现摘要需更正',
      lines: f.accounts.map((account, index) => ({
        account_id: account.id,
        account_version: 1,
        debit: index === 0 ? '25.00' : '0.00',
        credit: index === 1 ? '25.00' : '0.00',
      })),
    };
    const prepared = await checked(
      await request.post('/api/posting/manual-requests', { headers: f.headers, data: content }),
    );
    const item = prepared.request;
    expect(
      (
        await request.post(`/api/posting/manual-requests/${item.id}/withdraw`, {
          headers: f.reviewer,
          data: { expected_version: item.version, reason: '他人不可替代制单本人撤回' },
        })
      ).status(),
    ).toBe(403);
    await page.setViewportSize(viewport);
    await page.goto(
      `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`,
    );
    await openManualVoucher(page, f.book.name);
    const panel = page.getByRole('region', { name: '手工凭证办理', exact: true });
    await panel.getByText('录错或政策变更？撤回此待审凭证', { exact: true }).click();
    await expect(
      panel.getByRole('button', { name: '确认撤回待审凭证', exact: true }),
    ).toBeDisabled();
    await panel
      .getByLabel('撤回原因', { exact: true })
      .fill('虚构测试：摘要录错，保留原件并重新准备');
    await panel.getByRole('button', { name: '确认撤回待审凭证', exact: true }).click();
    await expect(panel.getByText('已撤回，保留历史', { exact: false })).toBeVisible();
    await expect(panel.getByRole('button', { name: '确认手工凭证审核', exact: true })).toHaveCount(
      0,
    );
    await expect(panel.getByRole('button', { name: '确认正式记账', exact: true })).toHaveCount(0);
    const after = await checked(
      await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.headers }),
      200,
    );
    expect(after.requests).toHaveLength(1);
    expect(after.requests[0].withdrawn).toBe(true);
    expect(after.requests[0].prepared_by).toBe(item.prepared_by);
    expect(after.requests[0].content).toEqual(item.content);
    expect(after.requests[0].withdrawal_reason).toContain('摘要录错');
    expect([403, 409]).toContain(
      (
        await request.post(`/api/posting/manual-requests/${item.id}/approve`, {
          headers: f.reviewer,
          data: {
            expected_version: after.requests[0].version,
            expected_policy_version: item.policy_version,
            reason: '已撤回不能继续审核',
          },
        })
      ).status(),
    );
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`manual-withdrawal-${viewport.name}.png`),
      fullPage: true,
    });
  });
  test(`手工凭证必须绑定制单责任并经独立复核再正式记账：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await formalFixture(request, true);
    await page.setViewportSize(viewport);
    const path = `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`;
    await page.goto(path);
    await openManualVoucher(page, f.book.name);
    const panel = page.getByRole('region', { name: '手工凭证办理', exact: true });
    await panel.getByLabel('凭证摘要', { exact: true }).fill(`${f.tag}-界面责任闭环`);
    await panel.getByLabel('制单说明', { exact: true }).fill('虚构原件已核对，本人承担制单责任');
    await panel.getByLabel('第 1 行正式科目', { exact: true }).selectOption(f.accounts[0].id);
    await panel.getByLabel('第 2 行正式科目', { exact: true }).selectOption(f.accounts[1].id);
    await panel.getByLabel('第 1 行借方金额', { exact: true }).fill('88.00');
    await panel.getByLabel('第 2 行贷方金额', { exact: true }).fill('88.00');
    await panel.getByRole('button', { name: '提交待复核', exact: true }).click();
    await expect(panel.getByRole('heading', { name: `${f.tag}-界面责任闭环` })).toBeVisible();
    await expect(panel.getByRole('button', { name: '确认手工凭证审核', exact: true })).toHaveCount(
      0,
    );
    const list = await checked(
      await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.headers }),
      200,
    );
    const item = list.requests[0];
    expect(item.prepared_by).toBe('local-accountant');
    expect(item.status).toBe('pending');
    expect(
      (
        await request.post(`/api/posting/manual-requests/${item.id}/approve`, {
          headers: f.headers,
          data: {
            expected_version: item.version,
            expected_policy_version: item.policy_version,
            reason: '禁止本人独立复核',
          },
        })
      ).status(),
    ).toBe(403);
    // Select the real local finance identity; no fabricated token or role projection.
    await page.route('**/api/session', async (route) => {
      const url = new URL(route.request().url());
      url.searchParams.set('persona', 'reviewer');
      await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
    });
    await page.goto(path);
    await openManualVoucher(page, f.book.name);
    await panel.getByRole('button', { name: '确认手工凭证审核', exact: true }).click();
    await expect(panel.getByRole('button', { name: '确认正式记账', exact: true })).toBeVisible();
    await panel.getByRole('button', { name: '确认正式记账', exact: true }).click();
    await expect(panel.getByText('已正式记账', { exact: false })).toBeVisible();
    const final = await checked(
      await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.headers }),
      200,
    );
    expect(final.requests[0].reviewed_by).toBe('local-reviewer');
    expect(final.requests[0].independent).toBe(true);
    expect(final.requests[0].posted_voucher_id).toBeTruthy();
    expect(
      (
        await checked(
          await request.get(`/api/books/${f.book.id}/exports`, { headers: f.headers }),
          200,
        )
      ).exports,
    ).toEqual([]);
    expect(
      (
        await request.get(`/api/books/${f.book.id}/manual-review-requests`, { headers: f.employee })
      ).status(),
    ).toBe(404);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`manual-policy-${viewport.name}.png`),
      fullPage: true,
    });
  });
  test(`正式账簿真实查询、原凭证与会计授权 CSV：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await formalFixture(request);
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const query = `book=${f.book.id}&company=${f.company.id}&period=2026-10`;
    if (viewport.name === 'desktop') {
      await page.goto(`/workspace/accounting?${query}`);
      const tools = page.locator('.book-more-actions');
      await tools.locator('summary').click();
      await tools.getByRole('button', { name: '正式账簿', exact: true }).click();
      await expect(page).toHaveURL(/\/workspace\/formal-books\?/);
    } else {
      await page.goto(`/workspace/formal-books?${query}`);
    }
    await expect(page.getByRole('heading', { name: '正式账簿', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const types = page.getByRole('navigation', { name: '账簿类型' });
    await expect(types.getByRole('button', { name: '科目余额表', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByText('10,100.00', { exact: true }).first()).toBeVisible();
    await page.getByRole('button', { name: '查看 1002 明细', exact: true }).click();
    await expect(types.getByRole('button', { name: '科目明细账', exact: true })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByText(`${f.tag}-真实接口正式凭证`, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '查看凭证 记-1', exact: true }).click();
    const detail = page.getByRole('region', { name: '正式凭证详情' });
    await expect(detail).toBeVisible();
    await expect(detail).toContainText(f.tag);
    await page.getByRole('button', { name: '关闭凭证详情', exact: true }).click();
    await types.getByRole('button', { name: '现金与银行日记账', exact: true }).click();
    await expect(page.getByText(`${f.tag}-真实接口正式凭证`, { exact: true })).toBeVisible();
    await types.getByRole('button', { name: '辅助账', exact: true }).click();
    await page.getByLabel('辅助项目', { exact: true }).fill('project');
    await page.getByLabel('辅助值', { exact: true }).fill('不存在的虚构项目');
    await page.getByRole('button', { name: '查询辅助账', exact: true }).click();
    await expect(
      page.getByText(/没有.*辅助|暂无.*辅助|没有.*记录|暂无.*记录/).last(),
    ).toBeVisible();
    await types.getByRole('button', { name: '科目余额表', exact: true }).click();
    await expect(page.getByText('10,100.00', { exact: true }).first()).toBeVisible();
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载当前账簿 CSV', exact: true }).click();
    const download = await downloadEvent;
    const destination = testInfo.outputPath(`formal-${viewport.name}.csv`);
    await download.saveAs(destination);
    const csv = await readFile(destination, 'utf8');
    expect(csv).toContain('1002');
    expect(csv).toContain('虚构银行存款');
    expect(csv).not.toContain('local-business-employee');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    expect(errors).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`formal-books-${viewport.name}.png`),
      fullPage: true,
    });
    const denied = await request.get(`/api/books/${f.book.id}/trial-balance`, {
      headers: f.employee,
    });
    expect(denied.status()).toBe(404);
    const deniedExport = await request.get(
      `/api/books/${f.book.id}/formal-books/export?kind=trial_balance`,
      { headers: f.employee },
    );
    expect(deniedExport.status()).toBe(404);
  });

  test(`月度交付真实冻结、ZIP 与独立客户签收：${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await monthlyFixture(request);
    await page.setViewportSize(viewport);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`/workspace/delivery?book=${f.book.id}&company=${f.company.id}&period=2026-10`);
    const panel = page.getByRole('region', { name: '月度交付包', exact: true });
    await expect(panel.getByRole('heading', { name: '月度交付包', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await panel
      .getByRole('combobox', { name: '交付报表版本', exact: true })
      .selectOption(f.report.report.id);
    const generate = panel.getByRole('button', { name: '生成本月交付包', exact: true });
    await expect(generate).toBeDisabled();
    await panel
      .getByLabel('交付或签收说明', { exact: true })
      .fill('虚构企业本月无业务，正式零余额账簿与已发布报表交付');
    await panel.getByRole('checkbox', { name: '我已核对本企业、本月份及本次版本' }).check();
    await expect(generate).toBeEnabled();
    await generate.click();
    await expect(panel.getByText('待客户签收', { exact: true })).toBeVisible();
    const overview = await checked(
      await request.get(`/api/books/${f.book.id}/delivery/monthly-package`, { headers: f.headers }),
      200,
    );
    expect(overview.packages).toHaveLength(1);
    const frozen = overview.packages[0];
    expect(frozen.book_id).toBe(f.book.id);
    expect(frozen.company_id).toBe(f.company.id);
    expect(frozen.contact_user_id).toBe('local-business-finance');
    expect(frozen.manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
    const downloading = page.waitForEvent('download');
    await panel.getByRole('button', { name: '下载交付包 V1', exact: true }).click();
    const download = await downloading;
    const destination = testInfo.outputPath(`monthly-${viewport.name}.zip`);
    await download.saveAs(destination);
    const bytes = await readFile(destination);
    expect(bytes.subarray(0, 2).toString('ascii')).toBe('PK');
    expect(bytes.includes(Buffer.from('manifest.json'))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`monthly-package-${viewport.name}.png`),
      fullPage: true,
    });
    const endpoint = `/api/books/${f.book.id}/delivery/monthly-package/${frozen.id}`;
    expect(
      (
        await request.post(`${endpoint}/acknowledge`, {
          headers: f.headers,
          data: { manifest_sha256: frozen.manifest_sha256, reason: '会计不能替代客户签收' },
        })
      ).status(),
    ).toBe(403);
    expect((await request.get(`${endpoint}/download`, { headers: f.employee })).status()).toBe(404);
    // Client and reviewer are distinct for the entire scenario.
    await choosePersona(page, 'finance');
    // Local demonstration sessions intentionally reset to accountant on reload.
    // Reconnect to the real finance session endpoint instead of faking claims.
    await page.route('**/api/session', async (route) => {
      const url = new URL(route.request().url());
      url.searchParams.set('persona', 'finance');
      await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
    });
    await page.goto(`/workspace/briefing?book=${f.book.id}&company=${f.company.id}&period=2026-10`);
    const clientPanel = page.getByRole('region', { name: '月度交付包', exact: true });
    await expect(clientPanel.getByText(/本月资料包（第 1 版）/)).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole('region', { name: '事务所账务概览', exact: true })).toHaveCount(0);
    await expect(page.getByRole('navigation', { name: '做账五步流水线', exact: true })).toHaveCount(
      0,
    );
    await expect(
      clientPanel.getByRole('button', { name: '生成本月交付包', exact: true }),
    ).toHaveCount(0);
    await expect(
      clientPanel.getByText(/待客户查看|客户已查看 · 待签收/, { exact: true }),
    ).toBeVisible({ timeout: 30_000 });
    await clientPanel
      .getByLabel('签收说明', { exact: true })
      .fill('客户已查看本月版本及原件索引，确认收到资料');
    await clientPanel
      .getByRole('checkbox', { name: '我已核对月份与版本，并确认收到本版全部文件' })
      .check();
    await clientPanel.getByRole('button', { name: '确认收到第 1 版', exact: true }).click();
    await expect(clientPanel.getByText('客户已签收', { exact: true })).toBeVisible();
    await expect(
      clientPanel.getByRole('button', { name: '确认收到第 1 版', exact: true }),
    ).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`monthly-customer-${viewport.name}.png`),
      fullPage: true,
    });
    const accepted = await checked(
      await request.get(`/api/books/${f.book.id}/delivery/monthly-package`, { headers: f.headers }),
      200,
    );
    expect(accepted.packages[0].acknowledgement.acknowledged_by).toBe('local-business-finance');
    expect(accepted.packages[0].external_import_status).toBe('separate_workflow');
    expect(accepted.packages[0].manifest_sha256).toBe(frozen.manifest_sha256);
    expect(errors).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });

  test(`未发布报表不能生成月度交付包：${viewport.name}`, async ({ page, request }) => {
    const f = await formalFixture(request);
    await page.setViewportSize(viewport);
    await page.goto(`/workspace/delivery?book=${f.book.id}&company=${f.company.id}&period=2026-10`);
    const panel = page.getByRole('region', { name: '月度交付包', exact: true });
    await expect(panel.getByRole('button', { name: '生成本月交付包', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(panel.getByRole('button', { name: '生成本月交付包', exact: true })).toBeDisabled();
    await expect(panel.getByText('交付前还需处理', { exact: true })).toBeVisible();
    const value = await checked(
      await request.get(`/api/books/${f.book.id}/delivery/monthly-package`, { headers: f.headers }),
      200,
    );
    expect(value.ready).toBe(false);
    expect(value.packages).toHaveLength(0);
  });
}
