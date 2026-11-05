import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import {
  choosePersona,
  openAccounting,
  openBookTools,
  openBusiness,
  openNavigation,
} from './ui-navigation';

async function openMonthlyTool(
  page: import('@playwright/test').Page,
  bookName: string,
  label: string,
) {
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(bookName);
  await page.getByRole('listitem').filter({ hasText: bookName }).click();
  await openBookTools(page);
  await page.getByRole('button', { name: label, exact: true }).click();
}

async function identities(request: APIRequestContext) {
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  return {
    owner,
    finance,
    headers: { Authorization: `Bearer ${owner.token}` },
    financeHeaders: { Authorization: `Bearer ${finance.token}` },
  };
}
async function companyAndBook(request: APIRequestContext, tag: string) {
  const identitiesValue = await identities(request);
  const { headers } = identitiesValue;
  const created = await request.post('/api/companies', {
    headers,
    data: { name: `月度合成企业 ${tag}`, tax_id: `SYNTHETIC-${tag}` },
  });
  expect(created.status()).toBe(201);
  const company = await created.json();
  const bookResponse = await request.post('/api/books', {
    headers,
    data: { name: `月度合成账套 ${tag}`, company_id: company.id, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  const reviewer = await (await request.get('/api/session?persona=reviewer')).json();
  return {
    ...identitiesValue,
    financeHeaders: { Authorization: `Bearer ${reviewer.token}` },
    company,
    book,
  };
}
async function grantReviewer(
  request: APIRequestContext,
  companyId: string,
  bookId: string,
  headers: Record<string, string>,
) {
  expect(
    (
      await request.put(`/api/companies/${companyId}/members`, {
        headers,
        data: { email: 'reviewer@demo.example.invalid', role: 'finance', department_id: null },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.put(`/api/books/${bookId}/members/local-reviewer`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
}
async function removeReviewer(
  request: APIRequestContext,
  companyId: string,
  bookId: string,
  headers: Record<string, string>,
) {
  expect(
    (await request.delete(`/api/books/${bookId}/members/local-reviewer`, { headers })).status(),
  ).toBe(200);
  expect(
    (
      await request.delete(`/api/companies/${companyId}/members/local-reviewer`, {
        headers,
      })
    ).status(),
  ).toBe(200);
}

test('跨企业月度矩阵分页覆盖全部授权账套，精确下钻同企业中的一套并刷新恢复', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const tag = crypto.randomUUID().slice(0, 8),
    { headers, company, book } = await companyAndBook(request, tag);
  const books = [book];
  for (let index = 1; index < 21; index++) {
    const response = await request.post('/api/books', {
      headers,
      data: { name: `月度合成账套 ${tag}-${index}`, company_id: company.id, period: '2026-10' },
    });
    expect(response.status()).toBe(201);
    books.push(await response.json());
  }
  const target = books[20];
  const nextResponse = await request.post('/api/books', {
    headers,
    data: { name: `十一月合成账套 ${tag}`, company_id: company.id, period: '2026-11' },
  });
  expect(nextResponse.status()).toBe(201);
  const november = await nextResponse.json();
  expect(
    (
      await request.post(`/api/books/${target.id}/imports`, {
        headers,
        data: {
          source_name: '合成月度矩阵来源',
          rows: [
            {
              external_id: `monthly-${tag}`,
              date: '2026-10-03',
              description: `办公费目标 ${tag}`,
              counterparty: '虚构供方',
              amount: '100.00',
              direction: 'expense',
              evidence_ref: '仅用于矩阵导航的合成原件',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${target.id}/suggestions`, { headers })).status()).toBe(
    200,
  );
  await page.goto('/workspace/monthly?period=2026-10');
  await page.getByLabel('搜索月度客户').fill(tag);
  await page.getByRole('button', { name: '查找', exact: true }).click();
  await expect(page.locator('.monthly-matrix-row')).toHaveCount(20);
  await expect(page.getByText('共 21 个授权账套 / 企业', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下一页客户', exact: true }).click();
  await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
  await page.getByRole('button', { name: '上一页客户', exact: true }).click();
  const row = page.locator(`[data-book="${target.id}"]`);
  await expect(row).toBeVisible();
  await expect(row).toContainText('尚未预检');
  await expect(row).not.toContainText('已完成');
  await page.screenshot({ path: 'artifacts/stage17-monthly-matrix-desktop.png', fullPage: false });
  await row.getByText('查看各环节明细', { exact: true }).click();
  await row.getByRole('button', { name: '会计复核：1 待审', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`book=${target.id}`));
  await expect(page.getByLabel('复核企业')).toHaveValue(company.id);
  await expect(page.getByLabel('复核期间')).toHaveValue('2026-10');
  await expect(page.getByText(`办公费目标 ${tag}`, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('复核企业')).toHaveValue(company.id);
  await expect(page.getByText(`办公费目标 ${tag}`, { exact: true })).toBeVisible();
  await page.goto('/workspace/monthly?period=2026-11');
  await page.getByLabel('搜索月度客户').fill(tag);
  await page.getByRole('button', { name: '查找', exact: true }).click();
  await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-11');
  await expect(page.locator('.monthly-matrix-row').first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'artifacts/stage17-monthly-matrix-mobile.png', fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page
    .locator(`[data-book="${november.id}"]`)
    .getByText('查看各环节明细', { exact: true })
    .click();
  await page
    .locator(`[data-book="${november.id}"]`)
    .getByRole('button', { name: '参考余额与差异', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: '2026-11 · 开放期间', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '返回客户月度进度', exact: true }).click();
  await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-11');
});

test('外部余额独立确认后比较，人工核对保留差异且新版本使旧快照失效', async ({ page, request }) => {
  test.setTimeout(90_000);
  const tag = crypto.randomUUID().slice(0, 8),
    { headers, financeHeaders, company, book } = await companyAndBook(request, tag);
  await grantReviewer(request, company.id, book.id, headers);
  try {
    await page.goto(`/workspace/balances?book=${book.id}`);
    await expect(
      page.getByRole('heading', { name: '参考余额与月度差异', exact: true }),
    ).toBeVisible();
    const pipeline = page.getByRole('navigation', { name: '做账五步流水线' });
    const closingStep = pipeline.getByRole('button', { name: '④ 对账与月结', exact: true });
    await expect(closingStep).toHaveAttribute('aria-current', 'step');
    await page.getByRole('button', { name: '录入外部余额', exact: true }).click();
    await page.getByLabel('外部余额来源').fill(`T+ 合成余额导出 ${tag}`);
    await page.getByLabel('外部余额原件依据').fill(`仅合成原件 ${tag}`);
    await page.getByLabel('外部余额录入依据').fill('合成期初现金余额，等待独立核查');
    await page.getByLabel('余额第 1 行科目').selectOption('1002');
    await page.getByLabel('余额第 1 行金额').fill('100.00');
    await expect(closingStep).toBeDisabled();
    await page.getByRole('button', { name: '保存并等待独立确认', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('等待另一位会计');
    await expect(page.getByRole('button', { name: '独立确认', exact: true })).toHaveCount(0);
    const closingResponse = await request.post(`/api/books/${book.id}/monthly/baselines`, {
      headers,
      data: {
        kind: 'closing',
        external_reference: `T+ 合成期末 ${tag}`,
        evidence_ref: '仅合成期末原件',
        reason: '明确保留两元合成差异',
        lines: [{ account_code: '1002', auxiliary_values: {}, balance: '102.00' }],
        idempotency_key: crypto.randomUUID(),
      },
    });
    expect(closingResponse.status()).toBe(201);
    await choosePersona(page, 'reviewer');
    await openMonthlyTool(page, book.name, '参考余额与月度差异');
    for (const kind of ['opening', 'closing']) {
      await page
        .getByLabel(`确认余额 v1 ${kind} 依据`, { exact: true })
        .fill('合成独立会计核对外部余额原件');
      const input = page.getByLabel(`确认余额 v1 ${kind} 依据`, { exact: true });
      await input.locator('..').getByRole('button', { name: '独立确认', exact: true }).click();
      await expect(input).toHaveCount(0);
    }
    await page.getByRole('button', { name: '生成核对快照', exact: true }).click();
    await expect(page.getByRole('heading', { name: '核对快照 v1', exact: true })).toBeVisible();
    await expect(page.getByRole('table', { name: '月度参考余额分层核对' }).first()).toContainText(
      '2.00',
    );
    await page.getByLabel('月度快照 v1 人工核对依据').fill('合成差异已经登记，保留差额待后续处理');
    await page.getByRole('button', { name: '记录人工核对', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('差异事实继续保留');
    const data = await (
      await request.get(`/api/books/${book.id}/monthly`, { headers: financeHeaders })
    ).json();
    expect(data.checks[0].report.lines[0].difference).toBe('2.00');
    expect(data.period_status).toBe('open');
    expect(data.reference.official_report).toBe(false);
    expect(
      (
        await request.post(`/api/books/${book.id}/monthly/baselines`, {
          headers,
          data: {
            kind: 'closing',
            external_reference: '新版本合成期末',
            evidence_ref: '新版本原件',
            reason: '修订需独立确认保留历史',
            lines: [{ account_code: '1002', balance: '101.00' }],
            idempotency_key: crypto.randomUUID(),
          },
        })
      ).status(),
    ).toBe(201);
    await page.getByRole('button', { name: '刷新余额', exact: true }).click();
    await expect(page.getByText('事实已变化', { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: 'artifacts/stage17-monthly-balances-mobile.png',
      fullPage: false,
    });
  } finally {
    await removeReviewer(request, company.id, book.id, headers);
  }
});

test('固定事项独立审批、生成未知结果重读不重复，新版本不改写已有草稿，撤权清空范围', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const tag = crypto.randomUUID().slice(0, 8),
    { headers, financeHeaders, company, book } = await companyAndBook(request, tag);
  await grantReviewer(request, company.id, book.id, headers);
  try {
    await page.goto(`/workspace/recurring?book=${book.id}`);
    await page.getByRole('button', { name: '新建计划', exact: true }).click();
    await page.getByLabel('固定事项名称').fill(`合成租金 ${tag}`);
    await page.getByLabel('固定事项凭证摘要').fill(`合成固定租金 ${tag}`);
    await page.getByLabel('固定事项原件依据').fill('明确合成租赁约定，仅验证流程');
    await page.getByLabel('固定事项版本依据').fill('真实金额由合成测试明确为一百元');
    await page.getByLabel('第 1 行会计科目', { exact: true }).selectOption('6602.01');
    await page.getByLabel('第 1 行借方金额', { exact: true }).fill('100.00');
    await page.getByLabel('第 2 行会计科目', { exact: true }).selectOption('1002');
    await page.getByLabel('第 2 行贷方金额', { exact: true }).fill('100.00');
    await page.getByRole('button', { name: '保存并等待独立审批', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('等待独立审批');
    await expect(page.getByRole('button', { name: '审批当前版本', exact: true })).toHaveCount(0);
    let overview = await (await request.get(`/api/books/${book.id}/monthly`, { headers })).json();
    const planId = overview.plans[0].id;
    await choosePersona(page, 'reviewer');
    await openMonthlyTool(page, book.name, '固定事项草稿');
    await page
      .getByLabel(`审批 合成租金 ${tag} v1 依据`, { exact: true })
      .fill('独立核查合成租赁金额期间及会计科目');
    await page.getByRole('button', { name: '审批当前版本', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('独立审批');
    let intercepted = false;
    await page.route(`**/api/books/${book.id}/monthly/plans/${planId}/generate`, async (route) => {
      if (!intercepted) {
        intercepted = true;
        expect((await route.fetch()).status()).toBe(201);
        await route.abort('failed');
      } else await route.continue();
    });
    await page.getByRole('button', { name: '生成 2026-10 草稿', exact: true }).click();
    await expect(page.getByRole('button', { name: '打开本月凭证', exact: true })).toBeVisible();
    await page.unroute(`**/api/books/${book.id}/monthly/plans/${planId}/generate`);
    overview = await (await request.get(`/api/books/${book.id}/monthly`, { headers })).json();
    const draftId = overview.plans[0].generated_current.draft_id;
    expect(
      (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts,
    ).toHaveLength(1);
    expect(
      (
        await request.post(`/api/books/${book.id}/exports`, {
          headers,
          data: { draft_ids: [draftId], idempotency_key: crypto.randomUUID() },
        })
      ).status(),
    ).toBe(409);
    await page.getByRole('button', { name: '创建修订版本', exact: true }).click();
    await page.getByLabel('固定事项版本依据').fill('合成租金变更为两百元，需要重新独立审批');
    await page
      .getByRole('form', { name: '固定事项计划录入' })
      .getByLabel('第 1 行借方金额', { exact: true })
      .fill('200.00');
    await page
      .getByRole('form', { name: '固定事项计划录入' })
      .getByLabel('第 2 行贷方金额', { exact: true })
      .fill('200.00');
    await page.getByRole('button', { name: '保存并等待独立审批', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: `合成租金 ${tag} · v2`, exact: true }),
    ).toBeVisible();
    const draft = await (
      await request.get(`/api/books/${book.id}/drafts/${draftId}`, { headers })
    ).json();
    expect(draft.lines[0].debit).toBe('100.00');
    await page.screenshot({ path: 'artifacts/stage17-recurring-desktop.png', fullPage: false });
    expect(
      (await request.delete(`/api/books/${book.id}/members/local-reviewer`, { headers })).status(),
    ).toBe(200);
    await page.getByRole('button', { name: '刷新计划', exact: true }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: `合成租金 ${tag} · v2`, exact: true }),
    ).toHaveCount(0);
    expect(
      (await request.get(`/api/books/${book.id}/monthly`, { headers: financeHeaders })).status(),
    ).toBe(404);
  } finally {
    await removeReviewer(request, company.id, book.id, headers);
  }
});

test('结构化余额文件核对后导入，保留UTF8原文和原件下载，仍等待独立确认', async ({
  page,
  request,
}) => {
  const tag = crypto.randomUUID().slice(0, 8),
    { headers, book } = await companyAndBook(request, tag);
  const content =
    '\uFEFFaccount_code,balance,auxiliary_values,period,currency,scope\n1002,-100.05,{},2026-09,CNY,posted\n';
  await page.goto(`/workspace/balances?book=${book.id}`);
  const panel = page.locator('.monthly-baseline-import');
  await panel.locator('summary').click();
  await panel.getByLabel('外部余额文件').setInputFiles({
    name: `合成余额-${tag}.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from(content, 'utf8'),
  });
  await panel.getByLabel('余额文件来源').fill('T+ 结构化合成余额，仅验证导入');
  await panel.getByLabel('余额文件原件依据').fill('明确合成外部余额文件');
  await panel.getByLabel('余额文件导入依据').fill('已核对合成九月人民币已过账余额');
  await expect(
    panel.getByRole('button', { name: '导入并等待独立确认', exact: true }),
  ).toBeDisabled();
  await panel.getByLabel('确认余额文件月份币种与过账范围').check();
  await panel.getByRole('button', { name: '导入并等待独立确认', exact: true }).click();
  await expect(panel.getByRole('status')).toContainText('等待另一位会计');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载余额原件', exact: true }).click();
  const download = await downloadPromise;
  expect((await readFile((await download.path())!)).equals(Buffer.from(content, 'utf8'))).toBe(
    true,
  );
  const overview = await (await request.get(`/api/books/${book.id}/monthly`, { headers })).json();
  expect(overview.baselines[0].confirmed).toBe(false);
  expect(overview.baselines[0].original.format).toBe('csv');
  expect(overview.reference.opening_id).toBeNull();
});

test('企业财务兼账套编辑身份保留客户门户，月度资料范围与企业事项一致', async ({
  page,
  request,
}) => {
  const tag = crypto.randomUUID().slice(0, 8),
    { headers, financeHeaders } = await identities(request);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const employeeHeaders = { Authorization: `Bearer ${employee.token}` };
  const context = await (
    await request.get('/api/business/context', { headers: financeHeaders })
  ).json();
  const company = context.companies.find(
    (entry: { can_finance: boolean; departments: unknown[] }) =>
      entry.can_finance && entry.departments.length,
  );
  expect(company).toBeTruthy();
  const created = await request.post('/api/books', {
    headers,
    data: { name: `双身份资料月度 ${tag}`, company_id: company.id, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  try {
    const description = `双身份待部门审批 ${tag}`;
    let response = await request.post('/api/business/events', {
      headers: employeeHeaders,
      data: {
        company_id: company.id,
        department_id: company.departments[0].id,
        template_id: 'reimbursement',
        idempotency_key: crypto.randomUUID(),
        payload: {
          occurred_on: '2026-10-08',
          description,
          counterparty: '明确合成供应商',
          amount: '100.00',
          direction: 'expense',
          payment_status: 'unpaid',
          category: 'office',
          purpose: '仅验证双身份合法资料范围',
        },
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    let event = await response.json();
    response = await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers: employeeHeaders,
        multipart: {
          file: {
            name: '明确合成资料.png',
            mimeType: 'image/png',
            buffer: Buffer.from(
              'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
              'base64',
            ),
          },
        },
      },
    );
    expect(response.status()).toBe(200);
    event = await response.json();
    response = await request.post(`/api/business/events/${event.id}/submit`, {
      headers: employeeHeaders,
      data: { expected_version: event.version },
    });
    expect(response.status()).toBe(200);
    expect((await response.json()).status).toBe('submitted');
    const matrix = await (
      await request.get(`/api/monthly/workbench?period=2026-10&search=${tag}`, {
        headers: financeHeaders,
      })
    ).json();
    const matrixRow = matrix.rows.find((entry: { book_id: string }) => entry.book_id === book.id);
    expect(matrixRow.materials.business_pending).toBeGreaterThanOrEqual(1);
    await page.goto('/');
    await choosePersona(page, 'finance');
    await expect(page.getByRole('navigation', { name: '客户服务', exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
    // An extra editor grant must not replace the finance user's primary
    // surface. Follow its real navigation and select the same company instead
    // of expecting the old firm-only monthly menu to reappear.
    await openBusiness(page);
    await page.getByRole('tab', { name: '企业财务', exact: true }).click();
    await page.getByLabel('筛选业务事项企业').selectOption(company.id);
    // The ordinary finance queue correctly defaults to ready-for-finance work.
    // Explicitly include waiting-for-department records for this comparison.
    await page.getByLabel('筛选业务事项状态').selectOption('');
    await expect(page.getByRole('tab', { name: '企业财务', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByLabel('筛选业务事项企业')).toHaveValue(company.id);
    await expect(page.getByLabel('筛选业务事项状态')).toHaveValue('');
    await expect(
      page.getByRole('button').filter({ has: page.getByText(description, { exact: true }) }),
    ).toBeVisible();
    await expect(page.getByRole('navigation', { name: '客户服务', exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
  } finally {
    expect(
      (
        await request.delete(`/api/books/${book.id}/members/local-business-finance`, { headers })
      ).status(),
    ).toBe(200);
  }
});
