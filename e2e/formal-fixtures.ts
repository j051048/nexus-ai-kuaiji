import { expect, type APIRequestContext, type APIResponse } from '@playwright/test';

export async function checked(response: APIResponse, expected = 201) {
  expect(response.status(), await response.text()).toBe(expected);
  return response.json();
}

async function identity(request: APIRequestContext, persona: string) {
  const session = await checked(await request.get(`/api/session?persona=${persona}`), 200);
  return { Authorization: `Bearer ${session.token}` };
}

/** Use the public responsibility flow; no fixture bypass of independent review. */
export async function reviewedManual(
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
  expect(prepared.request.prepared_by).not.toBe('local-business-finance');
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

/** Everything is created through the real local API; opening approval is a different identity. */
export async function formalFixture(
  request: APIRequestContext,
  zeroActivity = false,
  namePrefix = '正式账簿虚构',
) {
  const headers = await identity(request, 'accountant');
  const finance = await identity(request, 'finance');
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
  const voucher = zeroActivity
    ? null
    : await reviewedManual(request, headers, finance, {
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
  return { headers, finance, employee, tag, company, book, ledger, policy, accounts, voucher };
}

/** Legitimate no-activity month: no invented bank statement, receipt, approval or period lock. */
export async function monthlyFixture(request: APIRequestContext) {
  const f = await formalFixture(request, true);
  await checked(
    await request.put(`/api/books/${f.book.id}/service-plan`, {
      headers: f.headers,
      data: {
        expected_version: 0,
        lead_user_id: 'local-accountant',
        reviewer_user_id: 'local-business-finance',
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
      headers: f.finance,
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
      headers: f.finance,
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

/**
 * Appends a second physical month to the same long-term ledger through the real API:
 * period creation, its own frozen opening equal to the previous closing, then one voucher.
 */
export async function secondMonth(
  request: APIRequestContext,
  first: Awaited<ReturnType<typeof formalFixture>>,
  period = '2026-11',
) {
  const headers = first.headers;
  const created = await checked(
    await request.post(`/api/books/${first.book.id}/ledger/periods`, {
      headers,
      data: {
        period,
        name: `${first.tag}-${period}`,
        source_book_id: first.book.id,
        idempotency_key: `period-${crypto.randomUUID()}`,
      },
    }),
  );
  const bookId = created.book_id as string;
  const packageResponse = await checked(
    await request.post('/api/opening/packages', {
      headers,
      data: { ledger_id: first.ledger.id, policy_id: first.policy.id, target_period: period },
    }),
  );
  const version = await checked(
    await request.post(`/api/opening/packages/${packageResponse.package.id}/versions`, {
      headers,
      data: {
        idempotency_key: `opening-${crypto.randomUUID()}`,
        reason: '虚构次月期初，必须与上月期末一致',
        lines: first.accounts.map((account, index) => ({
          account_id: account.id,
          account_version: 1,
          year_opening: '0.00',
          ytd_debit: index === 0 ? '10100.00' : '0.00',
          ytd_credit: index === 1 ? '10100.00' : '0.00',
          opening: index === 0 ? '10100.00' : '-10100.00',
        })),
      },
    }),
  );
  const versionId = version.version.id;
  await checked(
    await request.post(`/api/opening/versions/${versionId}/validate`, { headers }),
    200,
  );
  const command = { expected_version: 1, reason: '虚构次月期初独立核准与冻结' };
  await checked(
    await request.post(`/api/opening/versions/${versionId}/submit`, { headers, data: command }),
    200,
  );
  await checked(
    await request.post(`/api/opening/versions/${versionId}/approve`, {
      headers: first.finance,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post(`/api/opening/versions/${versionId}/freeze`, {
      headers: first.finance,
      data: command,
    }),
    200,
  );
  await checked(
    await request.post('/api/posting/opening-bindings', {
      headers,
      data: { book_id: bookId, package_version_id: versionId, reason: '本地绑定次月冻结期初' },
    }),
  );
  const voucher = await reviewedManual(request, first.headers, first.finance, {
    book_id: bookId,
    command_key: `manual-${crypto.randomUUID()}`,
    voucher_date: `${period}-05`,
    description: `${first.tag}-次月正式凭证`,
    reason: '虚构次月业务，经会计复核后正式记账',
    lines: first.accounts.map((account, index) => ({
      account_id: account.id,
      account_version: 1,
      debit: index === 0 ? '200.00' : '0.00',
      credit: index === 1 ? '200.00' : '0.00',
    })),
  });
  return { bookId, voucher };
}
