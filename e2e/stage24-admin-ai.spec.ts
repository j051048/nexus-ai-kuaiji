import { expect, test, type APIRequestContext } from '@playwright/test';

test.setTimeout(60_000);

async function fixture(request: APIRequestContext, label: string) {
  const { token } = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${token}` };
  const response = await request.post('/api/companies', {
    headers,
    data: { name: `AI控制合成-${label}-${crypto.randomUUID().slice(0, 8)}`, tax_id: '' },
  });
  expect(response.status()).toBe(201);
  const company = await response.json();
  const controlResponse = await request.get(`/api/admin/companies/${company.id}/ai-control`, {
    headers,
  });
  expect(controlResponse.status()).toBe(200);
  return { company, headers, control: await controlResponse.json() };
}

test('企业AI页签按需读取，默认开启与供应商未配置分开，暂停恢复和费用策略都有原因及审计', async ({
  page,
  request,
}) => {
  const { company, headers } = await fixture(request, '真实控制');
  let reads = 0;
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  page.on('request', (value) => {
    const path = new URL(value.url()).pathname;
    if (!path.includes(`/api/admin/companies/${company.id}/ai-control`)) return;
    if (value.method() === 'GET') reads++;
    else writes.push({ path, body: value.postDataJSON() });
  });
  await page.goto('/workspace/home');
  await expect(page.getByRole('heading', { name: '事务所管理', exact: true })).toBeVisible();
  expect(reads).toBe(0);
  await page.goto(`/admin/organizations/${company.id}?tab=unknown`);
  await expect(page.getByRole('heading', { name: '企业人员', exact: true })).toBeVisible();
  expect(reads).toBe(0);
  await page.getByRole('tab', { name: 'AI用量与控制', exact: true }).click();
  await expect(page).toHaveURL(/\?tab=ai$/);
  await expect(page.getByRole('heading', { name: 'AI已开启', exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/stage24-admin-ai-desktop.png', fullPage: true });
  expect(reads).toBe(1);
  await expect(page.getByLabel('模型服务配置状态')).toContainText('服务未配置');
  await page.getByRole('button', { name: '暂停企业AI', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '暂停企业AI', exact: true });
  await expect(dialog.getByRole('button', { name: '确认暂停AI', exact: true })).toBeDisabled();
  await expect(dialog).toContainText('已送达供应商的请求无法撤回');
  await dialog.getByLabel('AI控制操作原因').fill('合成验证：核对本企业异常用量');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '确认暂停AI', exact: true }).click();
  await expect(page.getByRole('heading', { name: '管理员已暂停AI', exact: true })).toBeVisible();
  expect(writes[0].body).toMatchObject({
    enabled: false,
    expected_enabled: true,
    expected_company_version: 1,
    reason: '合成验证：核对本企业异常用量',
  });
  expect(typeof writes[0].body.expected_updated_at).toBe('string');
  await page.getByRole('button', { name: '恢复企业AI', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '恢复企业AI', exact: true });
  await dialog.getByLabel('AI控制操作原因').fill('合成验证：已核对，恢复受限处理');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '确认恢复AI', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'AI已开启', exact: true })).toBeVisible();
  await expect(page.getByLabel('模型服务配置状态')).toContainText('服务未配置');
  await page.getByRole('button', { name: '设置费用限额与提醒', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '预算与运行策略', exact: true });
  await dialog.getByLabel('开启企业月度模型额度控制', { exact: true }).check();
  await dialog.getByLabel('企业月度模型额度', { exact: true }).fill('200.00');
  await dialog.getByLabel('单次票据识别费用预留', { exact: true }).fill('2.00');
  await dialog.getByLabel('单次分录建议费用预留', { exact: true }).fill('3.00');
  await dialog.getByLabel('单次会计对话费用预留', { exact: true }).fill('1.00');
  await dialog.getByLabel('连续失败次数', { exact: true }).fill('5');
  await dialog.getByRole('button', { name: '保存预算策略', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '确认费用限额与提醒变更', exact: true });
  expect(writes).toHaveLength(2);
  await dialog.getByRole('button', { name: '返回修改', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '预算与运行策略', exact: true });
  await expect(dialog.getByLabel('企业月度模型额度', { exact: true })).toHaveValue('200.00');
  await dialog.getByRole('button', { name: '保存预算策略', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '确认费用限额与提醒变更', exact: true });
  await dialog.getByLabel('AI控制操作原因').fill('合成验证：试点月度成本与失败阈值');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '确认保存策略', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '月度费用限额已开启', exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('企业AI费用限额')).toContainText('200.000000');
  expect(writes).toHaveLength(3);
  expect(writes[2]).toMatchObject({
    path: `/api/admin/companies/${company.id}/ai-control/policy`,
    body: {
      monthly_limit: '200.00',
      failure_threshold: 5,
      reason: '合成验证：试点月度成本与失败阈值',
    },
  });
  const audit = await (
    await request.get(`/api/admin/audit?company_id=${company.id}&limit=100`, { headers })
  ).json();
  expect(
    audit.events.filter((value: { action: string }) => value.action === 'admin.ai.configure'),
  ).toHaveLength(2);
  expect(
    audit.events.filter((value: { action: string }) => value.action === 'admin.ai.policy'),
  ).toHaveLength(1);
  await page.reload();
  await expect(page.getByRole('tab', { name: 'AI用量与控制', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('heading', { name: 'AI已开启', exact: true })).toBeVisible();
});

test('客户企业管理员在智能识别页签只看到开关与外发说明，不显示 Token、费用与限额', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const { token } = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${token}` };
  const created = await request.post('/api/companies', {
    headers,
    data: { name: `AI客户层合成-${crypto.randomUUID().slice(0, 8)}`, tax_id: '' },
  });
  expect(created.status()).toBe(201);
  const company = await created.json();
  const granted = await request.put(`/api/companies/${company.id}/members`, {
    headers,
    data: { email: 'finance@demo.example.invalid', role: 'admin', department_id: null },
  });
  expect(granted.status()).toBe(200);

  try {
    await page.goto('/');
    await page.getByLabel('切换验证身份').selectOption('finance');
    await expect(page.getByRole('heading', { name: '企业工作台', exact: true })).toBeVisible();
    await page.evaluate((companyId) => {
      window.history.pushState({}, '', `/admin/organizations/${companyId}?tab=ai`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, company.id);

    await expect(page.getByRole('tablist', { name: '企业管理内容', exact: true })).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByRole('tab', { name: '智能识别与隐私', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByRole('heading', { name: '智能识别与隐私', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '智能识别已开启', exact: true })).toBeVisible();
    await expect(page.getByLabel('资料外发说明')).toContainText('费用与运行监控由事务所统一管理');
    await expect(page.getByRole('button', { name: '暂停企业AI', exact: true })).toBeVisible();
    await expect(page.getByLabel('企业Token与费用汇总')).toHaveCount(0);
    await expect(page.getByLabel('企业AI费用限额')).toHaveCount(0);
    await expect(page.getByLabel('模型服务配置状态')).toHaveCount(0);
    await expect(page.getByLabel('近期模型调用记录')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '设置费用限额与提醒', exact: true })).toHaveCount(
      0,
    );
    await page.screenshot({ path: 'artifacts/stage24-client-admin-ai.png', fullPage: true });

    await page.getByRole('button', { name: '暂停企业AI', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '暂停企业AI', exact: true });
    await expect(dialog).not.toContainText('费用仍需计入');
    await dialog.getByLabel('AI控制操作原因').fill('合成验证：客户侧暂停资料外发');
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: '确认暂停AI', exact: true }).click();
    await expect(page.getByRole('heading', { name: '智能识别已暂停', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '恢复企业AI', exact: true }).click();
    const restore = page.getByRole('dialog', { name: '恢复企业AI', exact: true });
    await restore.getByLabel('AI控制操作原因').fill('合成验证：客户侧恢复资料外发');
    await restore.getByRole('checkbox').check();
    await restore.getByRole('button', { name: '确认恢复AI', exact: true }).click();
    await expect(page.getByRole('heading', { name: '智能识别已开启', exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: 'artifacts/stage24-client-admin-ai-mobile.png',
      fullPage: true,
    });
  } finally {
    expect(
      (
        await request.delete(`/api/companies/${company.id}/members/local-business-finance`, {
          headers,
        })
      ).status(),
    ).toBe(200);
  }
});

test('手机查看Token、费用和异常，未知用量不计零，无管理权限时隐藏控制', async ({
  page,
  request,
}) => {
  const { company, control } = await fixture(request, '手机用量');
  const value = {
    ...control,
    ai: {
      ...control.ai,
      enabled: false,
      can_manage: false,
      ocr_ready: true,
      journal_ready: true,
      chat_ready: true,
    },
    can_manage_budget: false,
    usage: {
      ...control.usage,
      requests: 200,
      reported_requests: 199,
      input_tokens: 1234567,
      output_tokens: 76543,
      total_tokens: 1311110,
      estimated_cost: '12.345678',
      exposure: '12.345678',
      confirmed_cost: null,
      uncertain_requests: 2,
      overrun_count: 1,
    },
    alerts: [
      {
        id: 'synthetic-token-alert',
        state: 'open',
        severity: 'warning',
        message: '合成提醒：模型调用用量和费用回执待核对',
        last_seen_at: '2026-10-15T10:00:00Z',
      },
    ],
    costs: [
      {
        id: 'synthetic-cost',
        kind: 'chat',
        model: 'synthetic-model',
        state: 'uncertain',
        input_tokens: 321,
        output_tokens: null,
        estimated_cost: null,
        confirmed_cost: null,
        created_at: '2026-10-15T10:00:00Z',
      },
    ],
  };
  await page.route(`**/api/admin/companies/${company.id}/ai-control?**`, (route) =>
    route.fulfill({ json: value }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/admin/organizations/${company.id}?tab=ai`);
  await expect(page.getByRole('heading', { name: '管理员已暂停AI', exact: true })).toBeVisible();
  await expect(page.getByLabel('企业Token与费用汇总')).toContainText('1,234,567');
  await expect(page.getByLabel('企业Token与费用汇总')).toContainText('76,543');
  await expect(page.getByLabel('企业Token回执说明')).toContainText('1,311,110');
  await expect(page.getByLabel('企业Token与费用汇总')).toContainText('12.345678');
  await expect(page.getByLabel('企业AI异常提醒')).toContainText('2 次请求费用待核定');
  await expect(page.getByLabel('近期模型调用记录')).toContainText('未返回');
  await expect(page.getByRole('button', { name: '恢复企业AI', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '设置费用限额与提醒', exact: true })).toHaveCount(
    0,
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'artifacts/stage24-admin-ai-mobile.png', fullPage: true });
});

test('AI控制版本冲突只重新读取，不重复提交，撤权后清空统计与编辑器', async ({ page, request }) => {
  const { company, control } = await fixture(request, '并发撤权');
  let denied = false,
    patches = 0;
  await page.route(`**/api/admin/companies/${company.id}/ai-control**`, async (route) => {
    if (route.request().method() === 'GET')
      return denied
        ? route.fulfill({ status: 403, json: { detail: '合成管理权限已撤销' } })
        : route.fulfill({
            json: { ...control, usage: { ...control.usage, input_tokens: 87654321 } },
          });
    patches++;
    return route.fulfill({ status: 409, json: { detail: '合成其他管理员已修改' } });
  });
  await page.goto(`/admin/organizations/${company.id}?tab=ai`);
  await expect(page.getByLabel('企业Token与费用汇总')).toContainText('87,654,321');
  await page.getByRole('button', { name: '暂停企业AI', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '暂停企业AI', exact: true });
  await dialog.getByLabel('AI控制操作原因').fill('合成并发变更检查');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '确认暂停AI', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('alert').filter({ hasText: '已被其他管理员更新' })).toBeVisible();
  expect(patches).toBe(1);
  denied = true;
  await page.getByRole('button', { name: '刷新企业AI用量', exact: true }).click();
  await expect(page.getByLabel('企业Token与费用汇总')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '暂停企业AI', exact: true })).toHaveCount(0);
  await expect(page.getByText('正在读取企业AI用量与设置…', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('离开企业后丢弃迟到的AI统计，归档企业只显示元数据', async ({ page, request }) => {
  const a = await fixture(request, '迟到A'),
    b = await fixture(request, '当前B');
  let release!: () => void, arrived!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  await page.route(`**/api/admin/companies/${a.company.id}/ai-control?**`, async (route) => {
    arrived();
    await hold;
    await route
      .fulfill({ json: { ...a.control, usage: { ...a.control.usage, input_tokens: 99887766 } } })
      .catch(() => {});
  });
  await page.route(`**/api/admin/companies/${b.company.id}/ai-control?**`, (route) =>
    route.fulfill({
      json: {
        ...b.control,
        active: false,
        company: { ...b.control.company, status: 'archived' },
        ai: { ...b.control.ai, can_manage: false },
        usage: null,
        policy: null,
      },
    }),
  );
  try {
    await page.goto(`/admin/organizations/${a.company.id}?tab=ai`);
    await requested;
    await page.getByRole('button', { name: '企业列表', exact: true }).click();
    await page.locator('.admin-company-card').filter({ hasText: b.company.name }).click();
    await page.getByRole('tab', { name: 'AI用量与控制', exact: true }).click();
    await expect(page.getByText('企业已归档', { exact: true })).toBeVisible();
    release();
    await expect(page.getByLabel('企业Token与费用汇总')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '暂停企业AI', exact: true })).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('99,887,766');
  } finally {
    release();
  }
});

for (const paused of [true, false]) {
  test(`会计助手${paused ? '企业已暂停' : '供应商未配置'}分别说明，历史仍可读且不能发送问题`, async ({
    page,
    request,
  }) => {
    const { company, headers } = await fixture(request, paused ? '助手暂停' : '助手未配置');
    const create = await request.post('/api/assistant/conversations', {
      headers,
      data: {
        company_id: company.id,
        book_ids: [],
        event_ids: [],
        title: `合成助手状态${paused}`,
        idempotency_key: crypto.randomUUID(),
      },
    });
    expect(create.status()).toBe(201);
    const conversation = (await create.json()).conversation;
    const response = await request.get(
      `/api/assistant/conversations/${conversation.id}/workspace`,
      { headers },
    );
    expect(response.status()).toBe(200);
    const workspace = await response.json();
    const availability = {
      model_available: false,
      company_enabled: !paused,
      supplier_configured: paused,
      notice: paused ? '管理员已暂停企业AI' : '会计对话服务尚未配置',
    };
    await page.route(`**/api/assistant/conversations/${conversation.id}/workspace?**`, (route) =>
      route.fulfill({
        json: {
          ...workspace,
          detail: {
            ...workspace.detail,
            ...availability,
            context: { ...workspace.detail.context, ...availability },
          },
        },
      }),
    );
    await page.goto('/workspace/home');
    await page.getByRole('button', { name: '问会计助手', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '会计助手', exact: true });
    await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(company.id);
    await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
    await drawer.locator('.ga-history-row').filter({ hasText: conversation.title }).click();
    const input = drawer.getByLabel('向会计助手提问', { exact: true });
    await expect(input).toBeDisabled();
    await expect(input).toHaveAttribute(
      'placeholder',
      paused ? '该企业AI已暂停，请联系管理员恢复' : '会计对话服务尚未配置，请联系系统管理员',
    );
    await expect(
      drawer.getByRole('button', { name: '发送会计助手问题', exact: true }),
    ).toBeDisabled();
    await expect(drawer).toContainText(
      paused ? '历史会话和人工复核流程继续可用' : '会计对话服务尚未配置',
    );
  });
}
