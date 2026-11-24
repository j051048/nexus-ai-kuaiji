import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona, openBusiness } from './ui-navigation';

/**
 * 2C 场景二：报销单真的绑定到工作流版本。
 *
 * 全程走真实接口与本地演示数据（三家虚构示范企业）：
 * 1. 会计在流程中台发 v1；
 * 2. 员工提交一张 3,000.00 元报销单，同事务内自动绑定 v1；
 * 3. 中途发 v2，存量单必须仍按 v1 走完（部门跳过 → 财务审批 → 已完成）；
 * 4. 页面（1366 与 390）能看到「锁定版本 v1」，且小屏没有横向溢出。
 */

type Tokens = Record<'accountant' | 'employee' | 'department' | 'finance', string>;

type Setup = {
  companyId: string;
  companyName: string;
  eventId: string;
  submittedVersion: number;
  definitionVersion: number;
};

async function tokens(request: APIRequestContext): Promise<Tokens> {
  const result = {} as Tokens;
  for (const persona of ['accountant', 'employee', 'department', 'finance'] as const) {
    const body = await (await request.get(`/api/session?persona=${persona}`)).json();
    result[persona] = body.token as string;
  }
  return result;
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` };
}

async function publish(
  request: APIRequestContext,
  token: string,
  companyId: string,
  note: string,
  changes: { dual?: number; auto?: number } = {},
) {
  const overview = await (
    await request.get(`/api/workflow/overview?company_id=${companyId}`, { headers: auth(token) })
  ).json();
  const process = overview.processes.find(
    (item: { process_kind: string }) => item.process_kind === 'expense_reimbursement',
  );
  const routing = {
    ...process.template.routing,
    ...(changes.auto === undefined ? {} : { auto_approve_below_cents: changes.auto }),
    ...(changes.dual === undefined ? {} : { dual_approval_from_cents: changes.dual }),
  };
  const response = await request.post('/api/workflow/definitions', {
    headers: auth(token),
    data: {
      company_id: companyId,
      process_kind: 'expense_reimbursement',
      nodes: process.template.nodes,
      routing,
      note,
      publish: true,
    },
  });
  expect([200, 201], await response.text()).toContain(response.status());
  return (await response.json()) as { version: number };
}

async function setup(request: APIRequestContext, marks: Tokens): Promise<Setup> {
  const context = await (
    await request.get('/api/business/context', { headers: auth(marks.employee) })
  ).json();
  const company = (context.companies as { id: string; name: string; can_submit: boolean }[]).find(
    (item) => item.can_submit,
  );
  expect(company, '本地演示数据里应当有员工可提交的法人').toBeTruthy();
  const companyId = company!.id;

  const v1 = await publish(request, marks.accountant, companyId, 'v1 初始版本');

  const marker = crypto.randomUUID().slice(0, 8);
  const eventResponse = await request.post('/api/business/events', {
    headers: auth(marks.employee),
    data: {
      company_id: companyId,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-11-10',
        description: `合成报销单 ${marker}`,
        counterparty: '合成供应商',
        amount: '3000.00',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '办公耗材',
      },
    },
  });
  expect(eventResponse.status(), await eventResponse.text()).toBe(201);
  const event = await eventResponse.json();

  // 报销单提交前必须有一份真实附件（本地演示也走同一条规则）。
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers: auth(marks.employee),
      multipart: {
        file: {
          name: 'synthetic-proof.png',
          mimeType: 'image/png',
          // 1×1 透明 PNG：内容签名可识别，仅用于验证报销单与工作流绑定。
          buffer: Buffer.from(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
            'base64',
          ),
        },
      },
    },
  );
  expect([200, 201], await upload.text()).toContain(upload.status());
  const uploaded = await upload.json();

  const submitResponse = await request.post(`/api/business/events/${event.id}/submit`, {
    headers: auth(marks.employee),
    data: { expected_version: uploaded.version ?? event.version },
  });
  expect(submitResponse.status(), await submitResponse.text()).toBe(200);
  const submitted = await submitResponse.json();
  expect(submitted.workflow_instance?.definition_version).toBe(v1.version);
  expect(submitted.workflow_instance?.status).toBe('running');

  return {
    companyId,
    companyName: company!.name,
    eventId: event.id,
    submittedVersion: submitted.version,
    definitionVersion: v1.version,
  };
}

async function openStudio(page: Page, setup_: Setup) {
  await page.goto(`/admin/organizations/${setup_.companyId}?tab=workflow`);
  await expect(page.getByRole('heading', { name: '流程编排中台' })).toBeVisible();
  await expect(page.getByText(`锁定版本 v${setup_.definitionVersion}`).first()).toBeVisible();
}

for (const viewport of [
  { name: '1366', width: 1366, height: 900 },
  { name: '390', width: 390, height: 844 },
]) {
  test(`报销单锁定 v1 且发版后不受影响（${viewport.name}）`, async ({ page, request }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const marks = await tokens(request);
    const state = await setup(request, marks);

    // 中途发 v2：存量单必须仍按 v1 走完。
    const v2 = await publish(request, marks.accountant, state.companyId, 'v2 调低双人审批门槛', {
      dual: 300_000,
    });
    expect(v2.version).toBeGreaterThan(state.definitionVersion);

    const stillV1 = async () => {
      const detail = await (
        await request.get(`/api/business/events/${state.eventId}`, {
          headers: auth(marks.employee),
        })
      ).json();
      return {
        version: detail.workflow_instance?.definition_version,
        status: detail.workflow_instance?.status,
      };
    };
    expect(await stillV1()).toEqual({ version: state.definitionVersion, status: 'running' });

    // 部门审批（v1 单审批路径下只记录跳过）→ 财务审批 → 完成。
    const department = await request.post(
      `/api/business/events/${state.eventId}/department-approve`,
      { headers: auth(marks.department), data: { expected_version: state.submittedVersion } },
    );
    expect(department.status(), await department.text()).toBe(200);
    const afterDepartment = await department.json();

    const finance = await request.post(`/api/business/events/${state.eventId}/finance-approve`, {
      headers: auth(marks.finance),
      data: { expected_version: afterDepartment.version },
    });
    expect(finance.status(), await finance.text()).toBe(200);
    const afterFinance = await finance.json();
    expect(afterFinance.status).toBe('finance_approved');
    expect(afterFinance.workflow_instance?.definition_version).toBe(state.definitionVersion);
    expect(afterFinance.workflow_instance?.status).toBe('completed');
    const trail = (afterFinance.actions ?? []) as { stage?: string; decision?: string }[];
    expect(trail.map((row) => `${row.stage}:${row.decision}`)).toEqual([
      'department:approve',
      'finance:approve',
    ]);

    // 页面能看见存量实例锁定在 v1，且小屏没有横向溢出。
    await openStudio(page, state);
    await expect(page.getByText('生效中').first()).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
    await page.screenshot({
      path: `tmp_test/workflow-binding-${viewport.name}.png`,
      fullPage: true,
    });
  });
}

const uiReceipt = {
  name: '大额报销发票.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};

test('三角色真实界面办理大额报销：退回改金额重提、双人审批与移交生成唯一草稿（1366）', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1366, height: 900 });
  const marks = await tokens(request);
  const context = await (
    await request.get('/api/business/context', { headers: auth(marks.employee) })
  ).json();
  const targetCompany = (context.companies as { id: string; name: string }[]).find((item) =>
    item.name.includes('青屿设计'),
  );
  expect(targetCompany, '本地演示数据里应包含青屿设计').toBeTruthy();
  const companyId = targetCompany!.id;
  // 发布标准报销流程（>= 5,000 元走部门主管 + 财务双人审批）。
  const active = await publish(request, marks.accountant, companyId, '三角色真实界面回归基线');

  const marker = crypto.randomUUID().slice(0, 8);
  const description = `三角色大额报销 ${marker}`;

  // 1. 员工从自己的页面建单、传附件并提交（初始 3,000 元）。
  await page.goto('/');
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.locator('.intake-template-picker > summary').click();
  await page.getByRole('button', { name: /^员工报销/ }).click();
  await page.getByLabel('事项客户企业').selectOption({ label: targetCompany!.name });
  await page.getByLabel('发生日期', { exact: true }).fill('2026-10-12');
  await page.getByLabel('事项金额', { exact: true }).fill('3000.00');
  await page.getByLabel('事项摘要', { exact: true }).fill(description);
  await page.getByLabel('交易对方', { exact: true }).fill('合成差旅商');
  await page.getByText('更多信息（分类、用途、项目）', { exact: true }).click();
  await page.getByLabel('事项分类', { exact: true }).selectOption('office');
  await page.getByLabel('业务用途', { exact: true }).fill('三角色界面闭环验证');
  if (
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .count()
  )
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
  await page.getByRole('button', { name: '暂存草稿', exact: true }).click();
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '上传原始附件', exact: true })).toBeEnabled();
  await page.getByLabel('上传业务事项附件').setInputFiles(uiReceipt);
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(page.locator('.business-notice')).toContainText('事项已提交');

  // 2. 部门审批人从待办首页打开并退回补件。
  await page.goto('/workspace/home');
  await choosePersona(page, 'department');
  const deptReturnTask = page
    .locator('article.workspace-task')
    .filter({ hasText: description })
    .first();
  await expect(deptReturnTask).toBeVisible({ timeout: 20_000 });
  await deptReturnTask.locator('button').first().click();
  await page.getByLabel('退回原因', { exact: true }).fill('金额漏报一笔住宿费，请改大金额重提');
  await page.getByRole('button', { name: /退回/ }).last().click();
  await expect(page.locator('.business-notice')).toContainText('事项已退回');

  // 3. 员工回到自己的页面把金额改成 6,000.00 元（跨过 5,000 元双人门槛）重新提交。
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page
    .getByRole('button')
    .filter({ has: page.getByText(description, { exact: true }) })
    .click();
  await page.getByRole('button', { name: '修改并重新提交', exact: true }).click();
  await page.getByRole('button', { name: '修改信息', exact: true }).click();
  await page.getByLabel('事项金额', { exact: true }).fill('6000.00');
  if (
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .count()
  )
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
  await page.getByRole('button', { name: '修改后重新提交', exact: true }).click();
  await expect(page.locator('.business-notice')).toContainText('事项修改已保存并重新提交');

  // 4. 部门审批人从待办首页通过第一级审批。
  await page.goto('/workspace/home');
  await choosePersona(page, 'department');
  const deptApproveTask = page
    .locator('article.workspace-task')
    .filter({ hasText: description })
    .first();
  await expect(deptApproveTask).toBeVisible({ timeout: 20_000 });
  await deptApproveTask.locator('button').first().click();
  await page.getByRole('button', { name: '部门审批通过', exact: true }).click();
  await expect(page.locator('.business-notice')).toContainText('部门审批已通过');

  // 5. 财务从待办首页通过第二级审批并移交生成唯一草稿。
  await page.goto('/workspace/home');
  await choosePersona(page, 'finance');
  const financeTask = page
    .locator('article.workspace-task')
    .filter({ hasText: description })
    .first();
  await expect(financeTask).toBeVisible({ timeout: 20_000 });
  await financeTask.locator('button').first().click();
  await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
  await expect(page.locator('.business-notice')).toContainText('财务核验已通过');
  await page.getByLabel('交接到账套', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
  await expect(page.locator('.business-notice')).toContainText('已交接并关联凭证草稿');

  // 6. 核对接口态：原单已移交、新绑定实例为 6,000 元 completed 且不可逆终态、旧 3,000 元实例已作废留痕。
  const instancesBody = await (
    await request.get(`/api/workflow/instances?company_id=${companyId}`, {
      headers: auth(marks.accountant),
    })
  ).json();
  const eventsResponse = await request.get(
    `/api/business/events?scope=company&company_id=${companyId}`,
    { headers: auth(marks.finance) },
  );
  expect(eventsResponse.status(), await eventsResponse.text()).toBe(200);
  const eventsBody = await eventsResponse.json();
  const targetEvent = (
    eventsBody.events as { id: string; payload?: { description?: string }; status: string }[]
  ).find((item) => item.payload?.description === description);
  expect(targetEvent?.status).toBe('handed_off');
  const related = (
    instancesBody.items as {
      subject_id: string;
      amount_cents: number;
      status: string;
      definition_version: number;
      irreversible_reached: boolean;
    }[]
  ).filter((item) => item.subject_id === targetEvent!.id);
  expect(related.some((item) => item.amount_cents === 300_000 && item.status === 'rejected')).toBe(
    true,
  );
  expect(
    related.some(
      (item) =>
        item.amount_cents === 600_000 &&
        item.status === 'completed' &&
        item.definition_version === active.version &&
        item.irreversible_reached === true,
    ),
  ).toBe(true);
});
