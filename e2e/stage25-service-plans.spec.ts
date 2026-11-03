import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { choosePersona, openNavigation } from './ui-navigation';

test.setTimeout(90_000);

async function fixture(request: APIRequestContext, suffix: string) {
  const { token } = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${token}` };
  const tag = `服务单合成-${suffix}-${crypto.randomUUID().slice(0, 6)}`;
  const companyResponse = await request.post('/api/companies', {
    headers,
    data: { name: tag, tax_id: '' },
  });
  expect(companyResponse.status()).toBe(201);
  const company = await companyResponse.json();
  const bookResponse = await request.post('/api/books', {
    headers,
    data: { company_id: company.id, name: `${tag}-账套`, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  for (const [email, role] of [
    ['employee@demo.example.invalid', 'employee'],
    ['finance@demo.example.invalid', 'finance'],
  ]) {
    expect(
      (
        await request.put(`/api/companies/${company.id}/members`, {
          headers,
          data: { email, role, department_id: null },
        })
      ).status(),
    ).toBe(200);
  }
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-reviewer`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  const response = await request.get(`/api/books/${book.id}/service-plan`, { headers });
  expect(response.status()).toBe(200);
  return {
    company,
    book,
    headers,
    overview: await response.json(),
    url: `/workspace/service?book=${book.id}&company=${company.id}&period=2026-10`,
  };
}

test('真实月度服务单、显式生成资料、员工无发生声明与独立验收', async ({ page, request }) => {
  const f = await fixture(request, '流程');
  await page.goto(f.url);
  await expect(page.getByRole('heading', { name: /月度服务单$/ })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel('主办会计', { exact: true }).selectOption('local-accountant');
  await page.getByLabel('独立复核人', { exact: true }).selectOption('local-reviewer');
  await page.getByLabel('客户资料联系人', { exact: true }).selectOption('local-business-employee');
  await page.getByLabel('本月交付截止日期').fill('2026-11-08');
  await page.getByLabel('服务单变更原因').fill('合成验证：明确本月分工与交付时间');
  await page.getByRole('button', { name: '保存本月服务单', exact: true }).click();
  await expect(page.getByText(/本月版本 1/)).toBeVisible();
  await page.getByText('新建账套资料模板', { exact: true }).click();
  await page.getByLabel('资料模板名称').fill('虚构客户月度清单');
  await page.getByLabel('模板资料项目').fill('虚构客户销售资料核对');
  await page.getByLabel('资料默认责任人').selectOption('local-business-employee');
  await page.getByLabel('模板创建原因').fill('合成验证：按客户实际业务约定资料');
  await page.getByRole('button', { name: '保存资料模板', exact: true }).click();
  await page.getByLabel('选择资料模板').selectOption({ label: '虚构客户月度清单 · v1' });
  await expect(
    page.getByRole('button', { name: '生成 2026-10 资料清单', exact: true }),
  ).toBeDisabled();
  await page.getByLabel('本月清单生成说明').fill('合成验证：仅生成本月资料，不改账务');
  await page.getByRole('checkbox', { name: '已核对本月适用范围、资料责任人与截止日期' }).check();
  await page.getByRole('button', { name: '生成 2026-10 资料清单', exact: true }).click();
  await expect(page.getByRole('button', { name: /虚构客户销售资料核对/ })).toContainText('待提供');
  await page.screenshot({ path: 'artifacts/stage25-service-plan-desktop.png', fullPage: true });
  const permissions = await (
    await request.get(`/api/books/${f.book.id}/members`, { headers: f.headers })
  ).json();
  expect(JSON.stringify(permissions)).not.toContain('local-business-employee');
  await choosePersona(page, 'employee');
  await openNavigation(page);
  await page.getByRole('button', { name: '资料清单', exact: true }).click();
  await page.getByRole('button', { name: /虚构客户销售资料核对/ }).click();
  await page.getByLabel('资料提供方式').selectOption('no_activity');
  await page.getByLabel('资料提交说明').fill('已核对业务登记，本月未发生销售业务。');
  await page.getByRole('button', { name: '提交给独立验收人', exact: true }).click();
  await expect(page.getByRole('button', { name: /虚构客户销售资料核对/ })).toContainText(
    '待独立验收',
  );
  await expect(page.getByRole('button', { name: '确认资料验收', exact: true })).toHaveCount(0);
  await page.goto(f.url);
  await page.getByRole('button', { name: /虚构客户销售资料核对/ }).click();
  await expect(page.getByText('最近提交 · 本月无发生', { exact: true })).toBeVisible();
  await page.getByLabel('资料验收说明').fill('独立核对销售台账与本月业务清单，确认无发生。');
  await page.getByRole('button', { name: '确认资料验收', exact: true }).click();
  await expect(page.getByRole('button', { name: /虚构客户销售资料核对/ })).toContainText(
    '资料已验收',
  );
  const rows = await (
    await request.get(`/api/books/${f.book.id}/material-requests`, { headers: f.headers })
  ).json();
  expect(rows.rows[0].state).toBe('accepted');
  expect(rows.rows[0].latest_submission.submitted_by).toBe('local-business-employee');
  expect(rows.rows[0].last_review.reviewed_by).toBe('local-accountant');
});

test('服务单版本冲突不自动重提，撤权后清空人员资料及编辑内容', async ({ page, request }) => {
  const f = await fixture(request, '并发');
  let denied = false;
  let writes = 0;
  await page.route(`**/api/books/${f.book.id}/service-plan`, (route) => {
    if (route.request().method() === 'GET')
      return denied
        ? route.fulfill({ status: 404, json: { detail: '合成账套权限已撤销' } })
        : route.fulfill({ json: f.overview });
    writes++;
    return route.fulfill({ status: 409, json: { detail: '其他会计已更新本月分工' } });
  });
  await page.goto(f.url);
  await page.getByLabel('服务单变更原因').fill('合成验证并发调整');
  await page.getByRole('button', { name: '保存本月服务单', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '未自动重提' })).toBeVisible();
  expect(writes).toBe(1);
  await expect(page.getByLabel('服务单变更原因')).toHaveValue('');
  denied = true;
  await page.getByRole('button', { name: '刷新服务单', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '合成账套权限已撤销' })).toBeVisible();
  await expect(page.getByLabel('主办会计', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('本月资料清单', { exact: true })).toHaveCount(0);
  await expect(page.getByText('正在核对账套与服务分工…', { exact: true })).toHaveCount(0);
});

test('手机资料详情保持可读，过期依据不能直接验收，定向请求只查询指定编号', async ({
  page,
  request,
}) => {
  const f = await fixture(request, '手机');
  const material = {
    id: 'synthetic-precise-material',
    book_id: f.book.id,
    company_id: f.company.id,
    company_name: f.company.name,
    period: '2026-10',
    title: '合成定向银行资料',
    description: '请核对本月原始资料',
    required: true,
    due_on: '2026-10-10',
    assignee_user_id: 'local-business-employee',
    assignee_name: null,
    state: 'submitted',
    version: 2,
    evidence_current: false,
    latest_submission: {
      id: 'submission-synthetic',
      mode: 'provided',
      note: '原件事实已更新，等待重新提交',
      event_ids: ['event-synthetic'],
      evidence: [],
      submitted_by: 'local-business-employee',
      submitted_at: '2026-10-16T00:00:00Z',
    },
    last_review: null,
    permissions: { submit: false, review: true },
  };
  let read = '';
  await page.route('**/api/material-requests?**', (route) => {
    read = route.request().url();
    return route.fulfill({
      json: { rows: [material], pagination: { limit: 20, offset: 0, total: 1, has_more: false } },
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/materials?material=synthetic-precise-material');
  await expect(page.getByRole('article', { name: '资料详情：合成定向银行资料' })).toBeVisible();
  expect(new URL(read).searchParams.get('request_id')).toBe('synthetic-precise-material');
  await page.getByLabel('资料验收说明').fill('合成验证：已有资料变化，需重新提交');
  await expect(page.getByRole('button', { name: '确认资料验收', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '退回补充', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'artifacts/stage25-service-materials-mobile.png', fullPage: true });
});

test('员工直接提交银行CSV原件并独立验收，不生成业务金额或业务事项', async ({ page, request }) => {
  const f = await fixture(request, '原件');
  const created = await request.post(`/api/books/${f.book.id}/material-templates`, {
    headers: f.headers,
    data: {
      name: '虚构银行对账资料',
      items: [
        {
          key: 'bank_csv',
          title: '合成完整银行对账单',
          description: '直接提供银行导出的原件',
          required: true,
          due_day: 10,
          due_month_offset: 1,
          assignee_user_id: 'local-business-employee',
        },
      ],
      reason: '合成验证直接资料，无需创建金额事项',
    },
  });
  expect(created.status()).toBe(201);
  const template = await created.json();
  expect(
    (
      await request.post(`/api/books/${f.book.id}/material-templates/${template.id}/generate`, {
        headers: f.headers,
        data: { expected_version: template.version, reason: '合成验证：本月银行原件收集' },
      })
    ).status(),
  ).toBe(200);
  await page.goto('/workspace/home');
  await choosePersona(page, 'employee');
  await openNavigation(page);
  await page.getByRole('button', { name: '资料清单', exact: true }).click();
  await page.getByRole('button', { name: /合成完整银行对账单/ }).click();
  const file = {
    name: '虚构银行对账单.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('日期,摘要,收入,支出\n2026-10-16,虚构零业务核对,0.00,0.00\n', 'utf8'),
  };
  await page.getByLabel('上传月度资料原件').setInputFiles({
    name: '不可执行宏.xlsm',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('SYNTHETIC NON-DOCUMENT'),
  });
  await expect(page.getByRole('alert').filter({ hasText: '支持的格式' })).toBeVisible();
  await page.getByLabel('上传月度资料原件').setInputFiles(file);
  await expect(
    page.getByRole('checkbox', { name: `随本次提交：${file.name}`, exact: true }),
  ).toBeChecked();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByLabel('资料提交说明').fill('合成验证：本月完整银行对账单，覆盖整个期间。');
  await page.getByRole('button', { name: '提交给独立验收人', exact: true }).click();
  await expect(page.getByRole('button', { name: /合成完整银行对账单/ })).toContainText(
    '待独立验收',
  );
  await page.goto(f.url);
  await page.getByRole('button', { name: /合成完整银行对账单/ }).click();
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '下载原件', exact: true }).click(),
  ]);
  expect(await readFile((await download.path())!)).toEqual(file.buffer);
  await page.getByLabel('资料验收说明').fill('独立核对原件内容和资料期间，确认覆盖范围。');
  await page.getByRole('button', { name: '确认资料验收', exact: true }).click();
  await expect(page.getByRole('button', { name: /合成完整银行对账单/ })).toContainText(
    '资料已验收',
  );
  const values = await (
    await request.get(`/api/books/${f.book.id}/material-requests`, { headers: f.headers })
  ).json();
  expect(values.rows[0].latest_submission.event_ids).toEqual([]);
  expect(values.rows[0].latest_submission.attachments).toHaveLength(1);
  const unassigned = await (await request.get('/api/session?persona=department')).json();
  expect(
    (
      await request.get(
        `/api/material-requests/${values.rows[0].id}/attachments/${values.rows[0].latest_submission.attachments[0].id}/content`,
        { headers: { Authorization: `Bearer ${unassigned.token}` } },
      )
    ).status(),
  ).toBe(404);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const business = await (
    await request.get(`/api/business/events?scope=own&company_id=${f.company.id}`, {
      headers: { Authorization: `Bearer ${employee.token}` },
    })
  ).json();
  expect(business.events).toHaveLength(0);
});
