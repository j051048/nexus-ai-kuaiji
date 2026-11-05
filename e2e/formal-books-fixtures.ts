import { expect, type APIRequestContext, type APIResponse } from '@playwright/test';

export async function checked(response: APIResponse, expected = 201) {
  expect(response.status(), await response.text()).toBe(expected);
  return response.json();
}

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

export async function identity(request: APIRequestContext, persona: string) {
  const session = await checked(await request.get(`/api/session?persona=${persona}`), 200);
  return { Authorization: `Bearer ${session.token}` };
}

export async function formalFixture(
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
