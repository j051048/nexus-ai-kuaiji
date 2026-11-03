import { expect, test, type APIRequestContext } from '@playwright/test';
import { choosePersona } from './ui-navigation';

async function ownerHeaders(request: APIRequestContext) {
  const { token } = await (await request.get('/api/session?persona=accountant')).json();
  return { Authorization: `Bearer ${token}` };
}

test('后台企业创建、修订、暂停、恢复及归档保留历史，刷新保持管理路径', async ({
  page,
  request,
}) => {
  // The first local navigation includes Vite's cold CSS and lazy module transforms.
  // This case verifies administration behavior; production timing has its own benchmark.
  test.setTimeout(60_000);
  const tag = crypto.randomUUID().slice(0, 8);
  const name = `管理验证企业-${tag}`;
  const headers = await ownerHeaders(request);
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: '企业管理', exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await page.getByRole('button', { name: '新建企业', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '新建企业', exact: true });
  await dialog.getByLabel('企业名称', { exact: true }).fill(name);
  await dialog.getByLabel('统一社会信用代码／税号').fill(`SYNTHETIC-ADMIN-${tag}`);
  await dialog.getByRole('button', { name: '创建企业', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByLabel('搜索企业', { exact: true }).fill(name);
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await page.locator('.admin-company-card').filter({ hasText: name }).click();
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/organizations\//);
  const id = page.url().split('/').pop()!;
  await page.reload();
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '修改企业资料', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '修改企业资料', exact: true });
  await dialog.getByLabel('企业名称', { exact: true }).fill(`${name}修订`);
  await dialog.getByLabel('修改原因', { exact: true }).fill('修正合成企业名称');
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect(page.getByRole('heading', { name: `${name}修订`, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '暂停服务', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '暂停企业服务', exact: true });
  await expect(dialog.getByRole('button', { name: '暂停企业服务', exact: true })).toBeDisabled();
  await dialog.getByLabel('操作原因', { exact: true }).fill('合成测试暂停');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '暂停企业服务', exact: true }).click();
  await expect(page.getByRole('button', { name: '进入工作台', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '开通账号', exact: true })).toHaveCount(0);
  let result = await (await request.get(`/api/admin/companies/${id}`, { headers })).json();
  expect(result.company.status).toBe('suspended');
  await page.getByRole('button', { name: '恢复服务', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '恢复企业服务', exact: true });
  await dialog.getByLabel('操作原因', { exact: true }).fill('合成测试恢复');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '恢复企业服务', exact: true }).click();
  await expect(page.getByRole('button', { name: '进入工作台', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '归档企业', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '归档企业', exact: true });
  await dialog.getByLabel('操作原因', { exact: true }).fill('验证完成保留历史');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '归档企业', exact: true }).click();
  await expect(page.getByText('此企业已归档。', { exact: false })).toBeVisible();
  result = await (await request.get(`/api/admin/companies/${id}`, { headers })).json();
  expect(result.company.version).toBe(5);
  await page.getByRole('tab', { name: '操作记录', exact: true }).click();
  await expect(page.locator('.admin-audit-event')).toHaveCount(6);
  await expect(
    page.locator('.admin-audit-event').filter({ hasText: '企业AI默认开启' }),
  ).toHaveCount(1);
  await page.getByRole('button', { name: '企业列表', exact: true }).click();
  await page.getByLabel('搜索企业', { exact: true }).fill(name);
  await page.getByRole('button', { name: '查询', exact: true }).click();
  await page.getByLabel('企业状态', { exact: true }).selectOption('archived');
  await expect(page.locator('.admin-company-card')).toHaveCount(1);
  await expect(page.locator('.admin-company-card')).toContainText('已归档');
});

test('公司管理员只看所管企业，手机管理成员和部门不产生跨企业选择', async ({ page, request }) => {
  const headers = await ownerHeaders(request);
  const name = `限定企业-${crypto.randomUUID().slice(0, 8)}`;
  const created = await request.post('/api/companies', { headers, data: { name, tax_id: '' } });
  expect(created.status()).toBe(201);
  const company = await created.json();
  const granted = await request.put(`/api/companies/${company.id}/members`, {
    headers,
    data: { email: 'finance@demo.example.invalid', role: 'admin', department_id: null },
  });
  expect(granted.status()).toBe(200);
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await choosePersona(page, 'finance');
    await page.locator('.client-portal-settings > summary').click();
    await page.getByRole('button', { name: '企业与账号', exact: true }).click();
    await expect(page.getByRole('heading', { name: '企业管理', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '新建企业', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '事务所人员', exact: true })).toHaveCount(0);
    await page.locator('.admin-company-card').filter({ hasText: name }).click();
    await expect(page.getByRole('button', { name: '暂停服务', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '归档企业', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '添加已有成员', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '添加已有成员', exact: true });
    await dialog.getByLabel('成员登录邮箱', { exact: true }).fill('employee@demo.example.invalid');
    await dialog.getByLabel('权限角色', { exact: true }).selectOption('employee');
    await dialog.getByRole('button', { name: '保存授权', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(
      page.locator('.admin-member').filter({ hasText: 'employee@demo.example.invalid' }),
    ).toHaveCount(1);
    await page.getByRole('tab', { name: '部门与审批', exact: true }).click();
    await expect(page.getByLabel('客户企业', { exact: true }).locator('option')).toHaveCount(2);
    await expect(page.getByLabel('客户企业', { exact: true })).toHaveValue(company.id);
    await page.getByLabel('新建部门名称', { exact: true }).fill('试点采购部');
    await page.getByRole('button', { name: '新建部门', exact: true }).click();
    await expect(page.getByRole('heading', { name: '试点采购部', exact: true })).toBeVisible();
    const overflow = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('main input, main select, main section')]
        .filter((element) => element.getBoundingClientRect().right > window.innerWidth)
        .map((element) => ({
          tag: element.tagName,
          label: element.getAttribute('aria-label'),
          right: element.getBoundingClientRect().right,
        })),
    );
    expect(overflow).toEqual([]);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  } finally {
    expect(
      (
        await request.delete(`/api/companies/${company.id}/members/local-business-finance`, {
          headers,
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await request.delete(`/api/companies/${company.id}/members/local-business-employee`, {
          headers,
        })
      ).status(),
    ).toBe(200);
  }
});

test('员工直接访问管理路径显示无权限，激活链接缺失不显示开户表单', async ({ page }) => {
  await page.goto('/');
  await choosePersona(page, 'employee');
  await page.evaluate(() => {
    window.history.pushState({}, '', '/admin');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(
    page.getByRole('main').getByText('当前账号没有管理后台权限', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '新建企业', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '返回工作台', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/home/);
  await page.goto('/account-activation#token=incomplete-test-secret');
  await expect(page.getByText('激活链接不完整。', { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/account-activation$/);
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain('incomplete-test-secret');
});

test('并发修改冲突保留输入，刷新后重试不会覆盖旧版本', async ({ page, request }) => {
  const headers = await ownerHeaders(request);
  const name = `并发管理-${crypto.randomUUID().slice(0, 8)}`;
  const company = await (
    await request.post('/api/companies', { headers, data: { name, tax_id: '' } })
  ).json();
  await page.goto(`/admin/organizations/${company.id}`);
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '修改企业资料', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '修改企业资料', exact: true });
  await dialog.getByLabel('企业名称', { exact: true }).fill('本地尚未保存名称');
  await dialog.getByLabel('修改原因', { exact: true }).fill('人工检查版本冲突');
  expect(
    (
      await request.patch(`/api/admin/companies/${company.id}`, {
        headers,
        data: {
          name: `${name}另一位管理员`,
          tax_id: '',
          reason: '模拟并发修订',
          expected_version: 1,
        },
      })
    ).status(),
  ).toBe(200);
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect(dialog.getByRole('alert')).toBeVisible();
  await expect(dialog.getByLabel('企业名称', { exact: true })).toHaveValue('本地尚未保存名称');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '刷新企业详情', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: `${name}另一位管理员`, exact: true }),
  ).toBeVisible();
  const scopedTasks = page.waitForRequest(
    (requestValue) =>
      requestValue.url().includes('/api/workspace/tasks?') &&
      new URL(requestValue.url()).searchParams.get('company_id') === company.id,
  );
  await page.getByRole('button', { name: '进入工作台', exact: true }).click();
  await scopedTasks;
  // A newly created company has no work items, so it is absent from the paged
  // task-company picker. Its ID must still restrict the query and survive URL navigation.
  await expect(page).toHaveURL(new RegExp(`company=${company.id}`));
  await expect(page.getByRole('button', { name: '筛选待办企业', exact: true })).toContainText(
    '指定企业',
  );
});

test('开户失败可重试，激活链接仅临时显示，撤销邀请不会删除已有账号', async ({ page, request }) => {
  const headers = await ownerHeaders(request);
  const company = await (
    await request.post('/api/companies', {
      headers,
      data: { name: `开户验证-${crypto.randomUUID().slice(0, 8)}`, tax_id: '' },
    })
  ).json();
  const record = {
    id: 'synthetic-invite',
    name: '虚构员工',
    email: 'synthetic-invite@example.invalid',
    status: 'failed',
    error: '模拟供应商暂时不可用',
  };
  let created = false;
  let posted: Record<string, unknown> = {};
  const activation =
    'https://app.example.invalid/account-activation#request_id=synthetic-invite&token=synthetic-activation-secret&mode=existing';
  await page.route(/\/api\/admin\/account-requests(?:\/|\?|$)/, async (route) => {
    const requestValue = route.request();
    if (requestValue.method() === 'GET')
      return route.fulfill({
        json: { requests: created ? [record] : [], provisioning_enabled: true },
      });
    if (requestValue.url().endsWith('/retry')) {
      record.status = 'awaiting_activation';
      record.error = '';
      return route.fulfill({ json: { request: record, activation_url: activation } });
    }
    if (requestValue.url().endsWith('/cancel')) {
      record.status = 'cancelled';
      return route.fulfill({ json: { request: record } });
    }
    created = true;
    posted = requestValue.postDataJSON();
    return route.fulfill({ status: 201, json: { request: record } });
  });
  await page.goto(`/admin/organizations/${company.id}`);
  await page.getByRole('button', { name: '开通账号', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '开通账号', exact: true });
  await dialog.getByRole('radio', { name: /本人激活/ }).check();
  await dialog.getByLabel('姓名', { exact: true }).fill(record.name);
  await dialog.getByLabel('登录邮箱', { exact: true }).fill(record.email);
  await expect(dialog.locator('input[type=password]')).toHaveCount(0);
  await dialog.getByRole('button', { name: '生成激活链接', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText(record.error);
  expect(posted.company_id).toBe(company.id);
  expect(posted.company_role).toBe('employee');
  expect(posted.password).toBeUndefined();
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: '重试开通', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '新的账号激活链接', exact: true });
  await expect(dialog.getByLabel('账号激活链接')).toHaveValue(activation);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain('synthetic-activation-secret');
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await expect(page.getByLabel('账号激活链接')).toHaveCount(0);
  await page.getByRole('button', { name: '撤销邀请', exact: true }).click();
  await page
    .getByRole('dialog', { name: '撤销账号邀请', exact: true })
    .getByRole('button', { name: '确认撤销邀请', exact: true })
    .click();
  await expect(page.locator('.admin-request')).toContainText('已撤销');
  await expect(page.getByRole('button', { name: '重新生成链接', exact: true })).toHaveCount(0);
});

test('新用户激活先设本人密码，接受失败后重试不重复消费验证令牌', async ({ page }) => {
  const identityUrl = 'https://synthetic-auth.example.invalid';
  const id = '11111111-2222-4333-8444-555555555555';
  const email = 'activation@example.invalid';
  const user = {
    id,
    aud: 'authenticated',
    role: 'authenticated',
    email,
    email_confirmed_at: new Date().toISOString(),
    app_metadata: {},
    user_metadata: {},
    created_at: new Date().toISOString(),
    identities: [],
  };
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const token = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: id, aud: 'authenticated', exp: Math.floor(Date.now() / 1000) + 3600, aal: 'aal1' })}.synthetic-signature`;
  let verifications = 0,
    accepted = 0,
    passwordUpdates = 0;
  await page.route('**/api/auth/config', (route) =>
    route.fulfill({
      json: {
        mode: 'supabase',
        supabase_url: identityUrl,
        publishable_key: 'synthetic-public-key',
        admin_requires_mfa: false,
      },
    }),
  );
  await page.route(`${identityUrl}/auth/v1/**`, async (route) => {
    if (route.request().url().includes('/verify')) {
      verifications++;
      return route.fulfill({
        json: {
          access_token: token,
          refresh_token: 'synthetic-refresh',
          expires_in: 3600,
          token_type: 'bearer',
          user,
        },
      });
    }
    if (route.request().method() === 'PUT' && route.request().url().endsWith('/user')) {
      passwordUpdates++;
      expect(route.request().postDataJSON().password).toBe('synthetic-password-only');
      return route.fulfill({ json: user });
    }
    if (route.request().url().endsWith('/user')) return route.fulfill({ json: user });
    return route.fulfill({ status: 400, json: { message: 'Unsupported synthetic endpoint' } });
  });
  await page.route('**/api/admin/account-requests/synthetic-activation/accept', async (route) => {
    accepted++;
    expect(route.request().postDataJSON()).toEqual({ token: 'synthetic-acceptance-token' });
    return accepted === 1
      ? route.fulfill({ status: 503, json: { detail: '模拟授权服务暂时不可用' } })
      : route.fulfill({ json: { status: 'completed' } });
  });
  await page.route('**/api/me', (route) =>
    route.fulfill({
      json: {
        user: { id, name: '虚构激活用户', email },
        firms: [],
        current_firm_id: null,
        permissions: {
          manage_firm: false,
          create_company: false,
          create_book: false,
          manage_admin: false,
        },
        mode: 'supabase',
      },
    }),
  );
  await page.goto(
    '/account-activation#request_id=synthetic-activation&token=synthetic-acceptance-token&mode=invite&token_hash=synthetic-verification-hash',
  );
  await expect(page).toHaveURL(/\/account-activation$/);
  await page.getByLabel('设置密码', { exact: true }).fill('synthetic-password-only');
  await page.getByLabel('再次输入密码', { exact: true }).fill('synthetic-password-only');
  await page.getByRole('button', { name: '设置密码并接受授权', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('暂时无法确认本次操作结果');
  await page.getByLabel('设置密码', { exact: true }).fill('synthetic-password-only');
  await page.getByLabel('再次输入密码', { exact: true }).fill('synthetic-password-only');
  await page.getByRole('button', { name: '设置密码并接受授权', exact: true }).click();
  await expect(page.getByRole('heading', { name: '账号已激活', exact: true })).toBeVisible();
  expect(verifications).toBe(1);
  expect(accepted).toBe(2);
  expect(passwordUpdates).toBe(2);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toMatch(/synthetic-(acceptance|verification|refresh|password)/);
});

test('本人主动请求密码重置邮件，恢复回跳凭据不落盘且更新后重新登录', async ({ page, baseURL }) => {
  const identityUrl = 'https://synthetic-recovery.example.invalid';
  const user = {
    id: '11111111-2222-4333-8444-555555555555',
    aud: 'authenticated',
    role: 'authenticated',
    email: 'recovery@example.invalid',
    app_metadata: {},
    user_metadata: {},
    identities: [],
  };
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const token = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 })}.synthetic-recovery-signature`;
  let emails = 0,
    passwordChanges = 0,
    signouts = 0;
  await page.route('**/api/auth/config', (route) =>
    route.fulfill({
      json: {
        mode: 'supabase',
        supabase_url: identityUrl,
        publishable_key: 'synthetic-public-key',
      },
    }),
  );
  await page.route(`${identityUrl}/auth/v1/**`, async (route) => {
    const requestValue = route.request();
    const url = new URL(requestValue.url());
    if (url.pathname.endsWith('/recover')) {
      emails++;
      expect(requestValue.postDataJSON().email).toBe(user.email);
      expect(baseURL).toBeTruthy();
      expect(url.searchParams.get('redirect_to')).toBe(new URL('/account-recovery', baseURL).href);
      return route.fulfill({ json: {} });
    }
    if (url.pathname.endsWith('/user')) {
      if (requestValue.method() === 'PUT') {
        passwordChanges++;
        expect(requestValue.postDataJSON().password).toBe('synthetic-recovery-password');
      }
      return route.fulfill({ json: user });
    }
    if (url.pathname.endsWith('/logout')) {
      signouts++;
      expect(url.searchParams.get('scope')).toBe('local');
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fulfill({ status: 400, json: { message: 'Unsupported synthetic endpoint' } });
  });
  await page.goto('/account-recovery');
  await expect(page.getByRole('heading', { name: '本人找回密码', exact: true })).toBeVisible();
  expect(emails).toBe(0);
  await page.getByLabel('登录邮箱', { exact: true }).fill(user.email);
  await page.getByRole('button', { name: '发送密码重置邮件', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('如果该邮箱对应有效账号');
  expect(emails).toBe(1);
  await page.goto(
    `/account-recovery#type=recovery&access_token=${token}&refresh_token=synthetic-recovery-refresh`,
  );
  await expect(page.getByRole('heading', { name: '设置新的登录密码', exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/account-recovery$/);
  await page.getByLabel('新密码', { exact: true }).fill('synthetic-recovery-password');
  await page.getByLabel('再次输入新密码', { exact: true }).fill('synthetic-recovery-password');
  await page.getByRole('button', { name: '保存新密码', exact: true }).click();
  await expect(page.getByRole('heading', { name: '密码已更新', exact: true })).toBeVisible();
  expect(passwordChanges).toBe(1);
  expect(signouts).toBe(1);
  expect(emails).toBe(1);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain('synthetic-recovery');
  await expect(page.getByRole('link', { name: '返回登录', exact: true })).toBeVisible();
});

test('管理后台必须完成本人 MFA，错误验证码不发管理请求，成功后携带升级会话', async ({ page }) => {
  const identityUrl = 'https://synthetic-mfa.example.invalid';
  const user = {
    id: '11111111-2222-4333-8444-555555555555',
    email: 'mfa@example.invalid',
    aud: 'authenticated',
    role: 'authenticated',
    app_metadata: {},
    user_metadata: {},
    identities: [],
    factors: [
      {
        id: 'synthetic-factor',
        factor_type: 'totp',
        status: 'verified',
        friendly_name: 'Synthetic verifier',
      },
    ],
  };
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const token = (aal: string) =>
    `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600, aal })}.synthetic-signature`;
  const firstToken = token('aal1'),
    verifiedToken = token('aal2');
  let adminReads = 0,
    attempts = 0;
  const session = (access_token: string) => ({
    access_token,
    refresh_token: 'synthetic-mfa-refresh',
    expires_in: 3600,
    token_type: 'bearer',
    user,
  });
  await page.route('**/api/auth/config', (route) =>
    route.fulfill({
      json: {
        mode: 'supabase',
        supabase_url: identityUrl,
        publishable_key: 'synthetic-public-key',
        admin_requires_mfa: true,
        allow_firm_signup: false,
      },
    }),
  );
  await page.route(`${identityUrl}/auth/v1/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/token')) return route.fulfill({ json: session(firstToken) });
    if (url.pathname.endsWith('/user')) return route.fulfill({ json: user });
    if (url.pathname.endsWith('/challenge'))
      return route.fulfill({
        json: {
          id: 'synthetic-challenge',
          type: 'totp',
          expires_at: Math.floor(Date.now() / 1000) + 60,
        },
      });
    if (url.pathname.endsWith('/verify')) {
      attempts++;
      expect(route.request().postDataJSON()).toEqual({
        code: attempts === 1 ? '000000' : '123456',
        challenge_id: 'synthetic-challenge',
      });
      return attempts === 1
        ? route.fulfill({
            status: 422,
            json: { error_code: 'mfa_verification_failed', message: 'Synthetic invalid code' },
          })
        : route.fulfill({ json: session(verifiedToken) });
    }
    return route.fulfill({ status: 400, json: { message: 'Unsupported synthetic endpoint' } });
  });
  await page.route('**/api/me', (route) =>
    route.fulfill({
      json: {
        user: { id: user.id, name: '虚构 MFA 管理员', email: user.email },
        firms: [{ id: 'synthetic-firm', name: '虚构验证事务所', role: 'owner' }],
        current_firm_id: 'synthetic-firm',
        permissions: {
          manage_firm: true,
          manage_admin: true,
          create_company: true,
          create_book: true,
        },
        mode: 'supabase',
      },
    }),
  );
  await page.route('**/api/admin/companies?**', (route) => {
    adminReads++;
    expect(route.request().headers().authorization).toBe(`Bearer ${verifiedToken}`);
    return route.fulfill({ json: { companies: [], total: 0, limit: 25, offset: 0 } });
  });
  await page.goto('/admin');
  await page.getByLabel('工作邮箱', { exact: true }).fill(user.email);
  await page.getByLabel('密码', { exact: true }).fill('synthetic-mfa-password');
  await page.getByRole('button', { name: '登录管理后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '验证管理员身份', exact: true })).toBeVisible();
  expect(adminReads).toBe(0);
  await page.getByLabel('6 位动态验证码', { exact: true }).fill('000000');
  await page.getByRole('button', { name: '验证并进入后台', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('验证码不正确');
  expect(adminReads).toBe(0);
  await page.getByLabel('6 位动态验证码', { exact: true }).fill('123456');
  await page.getByRole('button', { name: '验证并进入后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '企业管理', exact: true })).toBeVisible();
  await expect.poll(() => adminReads).toBeGreaterThan(0);
  expect(attempts).toBe(2);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain('synthetic-mfa');
});
