import { choosePersona, openManagement } from './ui-navigation';
import { expect, test } from '@playwright/test';

const companyName = '青屿设计（虚构示例）';
const periodParts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit' }).formatToParts(new Date());
const period = `${periodParts.find(part => part.type === 'year')?.value}-${periodParts.find(part => part.type === 'month')?.value}`;
const policyFields = ['quota_enabled', 'monthly_limit', 'ocr_reserve', 'proposal_reserve', 'chat_reserve', 'failure_threshold', 'failure_window_minutes', 'backlog_threshold', 'backlog_age_minutes', 'queue_wait_minutes', 'worker_stale_seconds'];

test('真实本地费用监控：管理员设置预算，客户侧无入口且深链不渲染，员工与部门人员无入口', async ({ page, request }) => {
  await page.goto('/');
  await openManagement(page); await page.getByRole('button', { name: '费用与运行监控', exact: true }).click();
  const sessionResponse = await request.get('/api/session?persona=accountant');
  expect(sessionResponse.status()).toBe(200); const owner = await sessionResponse.json();
  const companiesResponse = await request.get('/api/operations/companies', { headers: { Authorization: `Bearer ${owner.token}` } });
  expect(companiesResponse.status()).toBe(200);
  const company = (await companiesResponse.json()).companies.find((item: { name: string }) => item.name === companyName);
  expect(company).toBeTruthy();
  await page.getByLabel('监控客户企业').selectOption({ label: companyName });
  const overviewResponse = await request.get(`/api/operations/companies/${company.id}/overview?period=${period}`, { headers: { Authorization: `Bearer ${owner.token}` } });
  expect(overviewResponse.status()).toBe(200); const initial = await overviewResponse.json();
  try {
    await expect(page.getByText('企业聚合监控，不开放员工票据原件', { exact: true })).toBeVisible();
    await expect(page.getByText('等待管理员配置报价', { exact: true })).toHaveCount(3);
    await page.getByRole('button', { name: '设置预算策略', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '预算与运行策略', exact: true });
    await dialog.getByLabel('开启企业月度模型额度控制').check();
    await dialog.getByLabel('企业月度模型额度', { exact: true }).fill('100.00');
    await dialog.getByLabel('单次票据识别费用预留').fill('1.00');
    await dialog.getByLabel('单次分录建议费用预留').fill('2.00');
    await dialog.getByLabel('单次会计对话费用预留').fill('0.50');
    await expect(dialog.getByText('模型单价尚未完整配置。开启额度控制后，仍需管理员配置报价才能发送模型请求。', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: '保存预算策略', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText('额度控制已开启', { exact: true })).toBeVisible();
    await expect(page.getByText('月度额度 ¥ 100.000000', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '汇总站内提醒', exact: true }).click();
    await expect(page.getByText('已汇总本企业当前运行状况和站内提醒。', { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('heading', { name: '费用可核对，运行可掌握。', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.screenshot({ path: 'artifacts/operations-local-no-provider.png', fullPage: true });
    await choosePersona(page, 'finance');
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    let financeTelemetryRequests = 0;
    page.on('request', requestValue => {
      if (new URL(requestValue.url()).pathname.endsWith('/telemetry')) financeTelemetryRequests++;
    });
    // 代账包干制：费用与运行监控是事务所内部工作区，客户侧导航不再提供入口。
    await openManagement(page);
    await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
    await page.evaluate(() => {
      window.history.pushState({}, '', '/workspace/operations');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await expect(page.getByText('费用与运行监控由事务所管理', { exact: true })).toBeVisible();
    await expect(page.getByLabel('监控客户企业')).toHaveCount(0);
    await expect(page.getByText('额度控制已开启', { exact: true })).toHaveCount(0);
    expect(financeTelemetryRequests).toBe(0);
    await choosePersona(page, 'employee');
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
    await choosePersona(page, 'department');
    await expect(page.getByRole('heading', { name: '部门审批', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
  } finally {
    const currentResponse = await request.get(`/api/operations/companies/${company.id}/overview?period=${period}`, { headers: { Authorization: `Bearer ${owner.token}` } });
    expect(currentResponse.status()).toBe(200); const current = await currentResponse.json();
    const restore = await request.put(`/api/operations/companies/${company.id}/policy`, { headers: { Authorization: `Bearer ${owner.token}` }, data: { ...Object.fromEntries(policyFields.map(field => [field, initial.policy[field]])), expected_version: current.policy.version } });
    expect(restore.status()).toBe(200);
  }
});

test('显式合成监控响应：未知费用不置零，提醒处置和账单核定保留版本及依据', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1100 });
  let ackRequests = 0; let reconciliationRequests = 0; let policyRequests = 0;
  let alertState = 'open'; let confirmed: string | null = null; let reconciliationReason: string | null = null;
  let currentPolicyVersion = 1;
  try {
  await page.route('**/api/operations/companies/*/overview?**', async route => {
    const real = await route.fetch(); expect(real.status()).toBe(200); const authorized = await real.json();
    const canManage = authorized.can_manage_budget === true;
    const created = '2026-10-12T01:00:00Z';
    await route.fulfill({ response: real, json: {
      ...authorized,
      policy: { ...authorized.policy, version: currentPolicyVersion, quota_enabled: true, monthly_limit: '100.00', ocr_reserve: '1.00', proposal_reserve: '2.00' },
      usage: { requests: 3, input_tokens: 100, output_tokens: null, reported_requests: 1, uncertain_requests: confirmed === null ? 1 : 0, reserved_cost: confirmed === null ? '1.000000' : '0.000000', carried_reserved_cost: '2.000000', estimated_cost: '0.000001', confirmed_cost: confirmed, exposure: confirmed === null ? '3.000001' : '2.800001', available: confirmed === null ? '96.999999' : '97.199999', overrun_count: 0 },
      alerts: [{ id: 'synthetic-monitor-alert', code: 'queue_backlog', severity: 'warning', state: alertState, message: '合成测试：处理队列需要核对', opened_at: created, last_seen_at: created, version: alertState === 'open' ? 1 : 2, reason: alertState === 'open' ? null : '已核对合成任务记录，继续观察后台' }],
      costs: [{ id: 'synthetic-monitor-cost', kind: 'ocr', model: '合成费用示例（非供应商账单）', state: confirmed === null ? 'uncertain' : 'reconciled', reserved_cost: '1.000000', estimated_cost: null, confirmed_cost: confirmed, input_tokens: null, output_tokens: null, created_at: created, version: confirmed === null ? 1 : 2, reason: reconciliationReason, can_reconcile: canManage }, { id: 'synthetic-active-cost', kind: 'proposal', model: '合成处理中费用示例', state: 'sent', reserved_cost: '1.000000', estimated_cost: null, confirmed_cost: null, input_tokens: null, output_tokens: null, created_at: created, version: 1, reason: null, can_reconcile: false }],
    } });
  });
  await page.route('**/api/operations/alerts/synthetic-monitor-alert/acknowledge', async route => {
    expect(route.request().postDataJSON()).toEqual({ reason: '已核对合成任务记录，继续观察后台', expected_version: 1 }); ackRequests += 1; alertState = 'acknowledged';
    await route.fulfill({ status: 200, json: { id: 'synthetic-monitor-alert', state: alertState, version: 2 } });
  });
  await page.route('**/api/operations/costs/synthetic-monitor-cost/reconcile', async route => {
    expect(route.request().postDataJSON()).toEqual({ confirmed_cost: '0.80', reason: '合成账单 UI 测试依据，非真实供应商扣费', expected_version: 1 }); reconciliationRequests += 1; confirmed = '0.800000'; reconciliationReason = '合成账单 UI 测试依据，非真实供应商扣费';
    await route.fulfill({ status: 200, json: { id: 'synthetic-monitor-cost', state: 'reconciled', version: 2 } });
  });
  await page.route('**/api/operations/companies/*/policy', async route => {
    const body = route.request().postDataJSON(); expect(body.expected_version).toBe(1); expect(body.monthly_limit).toBe('120.00'); policyRequests += 1;
    currentPolicyVersion = 2;
    await route.fulfill({ status: 409, json: { detail: '合成测试：预算版本已改变，请读取最新策略' } });
  });
  await page.goto('/');
  await openManagement(page); await page.getByRole('button', { name: '费用与运行监控', exact: true }).click();
  await page.getByLabel('监控客户企业').selectOption({ label: companyName });
  const summary = page.getByRole('region', { name: '企业模型费用与额度', exact: true });
  await expect(summary.getByText('¥ 0.000001', { exact: true })).toBeVisible();
  await expect(summary.getByText('待核定', { exact: true })).toBeVisible();
  await expect(summary.getByText('往月待核定预留 ¥ 2.000000', { exact: true })).toBeVisible();
  expect(ackRequests).toBe(0); expect(reconciliationRequests).toBe(0); expect(policyRequests).toBe(0);
  await page.getByRole('button', { name: '记录提醒处置依据', exact: true }).click();
  const ackDialog = page.getByRole('dialog', { name: '记录站内提醒处置依据', exact: true });
  await expect(ackDialog.getByRole('button', { name: '保存提醒处置', exact: true })).toBeDisabled();
  await ackDialog.getByLabel('站内提醒处置依据').fill('已核对合成任务记录，继续观察后台');
  await ackDialog.getByRole('button', { name: '保存提醒处置', exact: true }).click();
  await expect(ackDialog).toHaveCount(0); expect(ackRequests).toBe(1);
  await expect(page.getByText('已记录处置', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '核定此项费用', exact: true })).toHaveCount(1);
  await page.getByRole('button', { name: '核定此项费用', exact: true }).click();
  const costDialog = page.getByRole('dialog', { name: '核定供应商模型费用', exact: true });
  await costDialog.getByLabel('供应商费用核定金额').fill('0.80');
  await costDialog.getByLabel('供应商费用核对依据').fill('合成账单 UI 测试依据，非真实供应商扣费');
  await costDialog.getByRole('button', { name: '保存费用核定', exact: true }).click();
  await expect(costDialog).toHaveCount(0); expect(reconciliationRequests).toBe(1);
  await expect(summary.getByText('¥ 0.800000', { exact: true })).toBeVisible();
  await expect(page.getByText('合成账单 UI 测试依据，非真实供应商扣费', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '设置预算策略', exact: true }).click();
  const policyDialog = page.getByRole('dialog', { name: '预算与运行策略', exact: true });
  await policyDialog.getByLabel('企业月度模型额度', { exact: true }).fill('120.00');
  await policyDialog.getByRole('button', { name: '保存预算策略', exact: true }).click();
  await expect(policyDialog.getByRole('alert')).toContainText('预算版本已改变');
  await expect(policyDialog.getByLabel('企业月度模型额度', { exact: true })).toHaveValue('120.00');
  expect(policyRequests).toBe(1);
  await policyDialog.getByRole('button', { name: '取消', exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'artifacts/operations-synthetic-monitor-ui.png', fullPage: true });
  await choosePersona(page, 'finance');
  await openManagement(page);
  await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
  await expect(page.getByText('合成费用示例（非供应商账单）', { exact: true })).toHaveCount(0);
  await choosePersona(page, 'employee');
  await expect(page.getByText('合成费用示例（非供应商账单）', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '费用与运行监控', exact: true })).toHaveCount(0);
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
  }
});
