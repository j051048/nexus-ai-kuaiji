import { expect, test, type Page, type APIRequestContext } from '@playwright/test';

test.setTimeout(60_000);

// The identity provider is synthetic; all application calls stay on the local test server.
async function syntheticAuth(page: Page, request: APIRequestContext, initialAal = 'aal1') {
  const events: string[] = [];
  page.on('request', (req) => {
    if (/\/api\/|\/auth\/v1\//.test(req.url()))
      events.push('request ' + req.method() + ' ' + new URL(req.url()).pathname);
  });
  page.on('response', (res) => {
    if (/\/api\/|\/auth\/v1\//.test(res.url()))
      events.push('response ' + res.status() + ' ' + new URL(res.url()).pathname);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') events.push('console ' + msg.text());
  });
  page.on('pageerror', (error) => events.push('pageerror ' + error.message));
  const local = await (await request.get('/api/session')).json();
  const me = await (
    await request.get('/api/me', { headers: { Authorization: `Bearer ${local.token}` } })
  ).json();
  const identityUrl = 'https://mfa-local-test.example.invalid';
  const user = {
    ...me.user,
    aud: 'authenticated',
    role: 'authenticated',
    app_metadata: {},
    user_metadata: {},
    identities: [],
    factors: [{ id: 'test-factor', factor_type: 'totp', status: 'verified' }],
  };
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const token = (aal: string) =>
    `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600, aal })}.test-signature`;
  const first = token(initialAal),
    upgraded = token('aal2');
  const authSession = (access_token: string) => ({
    access_token,
    refresh_token: 'synthetic-refresh',
    expires_in: 3600,
    token_type: 'bearer',
    user,
  });
  let writes = 0,
    verified = false,
    adminReads = 0;
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    expect(url.hostname).toBe('127.0.0.1');
    if (url.pathname === '/api/auth/config')
      return route.fulfill({
        json: {
          mode: 'supabase',
          supabase_url: identityUrl,
          publishable_key: 'synthetic-key',
          admin_requires_mfa: true,
        },
      });
    if (url.pathname === '/api/me') return route.fulfill({ json: { ...me, mode: 'supabase' } });
    if (url.pathname === '/api/companies' && route.request().method() === 'POST') {
      writes++;
      if (!verified)
        return route.fulfill({
          status: 403,
          json: { code: 'admin_mfa_required', detail: '管理操作需要二次验证' },
        });
      expect(route.request().headers().authorization).toBe(`Bearer ${upgraded}`);
    }
    if (url.pathname.startsWith('/api/admin/')) {
      adminReads++;
      if (!verified)
        return route.fulfill({
          status: 403,
          json: { code: 'admin_mfa_required', detail: '需要重新二次验证' },
        });
    }
    return route.continue({
      headers: { ...route.request().headers(), authorization: `Bearer ${local.token}` },
    });
  });
  await page.route(`${identityUrl}/auth/v1/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/token')) return route.fulfill({ json: authSession(first) });
    if (path.endsWith('/user')) return route.fulfill({ json: user });
    if (path.endsWith('/challenge'))
      return route.fulfill({
        json: {
          id: 'test-challenge',
          type: 'totp',
          expires_at: Math.floor(Date.now() / 1000) + 60,
        },
      });
    if (path.endsWith('/verify')) {
      if (route.request().postDataJSON().code !== '123456')
        return route.fulfill({ status: 422, json: { message: 'Invalid test code' } });
      verified = true;
      return route.fulfill({ json: authSession(upgraded) });
    }
    return route.fulfill({
      status: 400,
      json: { message: 'Unsupported synthetic auth operation' },
    });
  });
  return { writes: () => writes, adminReads: () => adminReads, user, events };
}

test('客户开通读目录无需MFA；管理写入原地验证，保留表单且不自动重放', async ({ page, request }) => {
  const auth = await syntheticAuth(page, request);
  await page.goto('/workspace/onboarding');
  await page.getByLabel('工作邮箱', { exact: true }).fill('local-mfa@example.invalid');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-password');
  await page.getByRole('button', { name: '登录工作空间', exact: true }).click();
  await expect(page.getByRole('heading', { name: '客户标准化开通', exact: true })).toBeVisible();
  await expect(page.getByLabel('目标企业').locator('option').first()).toBeAttached();
  expect(auth.adminReads()).toBe(0);
  const company = `虚构MFA本地回归-${Date.now()}`;
  await page.getByLabel('企业名称', { exact: true }).fill(company);
  await page.getByRole('button', { name: '建立客户档案', exact: true }).click();
  await expect(page.getByRole('button', { name: '完成二次验证', exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/workspace\/onboarding/);
  await expect(page.getByLabel('企业名称', { exact: true })).toHaveValue(company);
  expect(auth.writes()).toBe(1);
  await page.getByRole('button', { name: '完成二次验证', exact: true }).click();
  await page.getByLabel('6 位动态验证码', { exact: true }).fill('000000');
  await page.getByRole('button', { name: '验证并继续', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: '管理操作二次验证', exact: true }).getByRole('alert'),
  ).toContainText('验证码不正确');
  await page.getByLabel('6 位动态验证码', { exact: true }).fill('123456');
  await page.getByRole('button', { name: '验证并继续', exact: true }).click();
  await expect(page.getByRole('button', { name: '返回原页面', exact: true }))
    .toBeVisible({ timeout: 10000 })
    .catch(async (failure) => {
      await test
        .info()
        .attach('mfa-request-progress', {
          body: Buffer.from(auth.events.join('\n')),
          contentType: 'text/plain',
        });
      throw failure;
    });
  await page.getByRole('button', { name: '返回原页面', exact: true }).click();
  expect(auth.writes()).toBe(1);
  await expect(page.getByLabel('企业名称', { exact: true })).toHaveValue(company);
  await expect(page).toHaveURL(/\/workspace\/onboarding/);
  await page.getByRole('button', { name: '建立客户档案', exact: true }).click();
  await expect.poll(auth.writes).toBe(2);
  await expect(page.getByLabel('企业名称', { exact: true })).toHaveValue('');
});

test('后台已认为aal2但服务要求再次验证时，不闪回登录也不绕过挑战', async ({ page, request }) => {
  const auth = await syntheticAuth(page, request, 'aal2');
  await page.goto('/admin');
  await page.getByLabel('工作邮箱', { exact: true }).fill('local-mfa@example.invalid');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-password');
  await page.getByRole('button', { name: '登录管理后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '验证管理员身份', exact: true })).toBeVisible();
  await expect(page.getByLabel('6 位动态验证码', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/admin$/);
  expect(auth.adminReads()).toBe(1);
  await page.getByLabel('6 位动态验证码', { exact: true }).fill('123456');
  await page.getByRole('button', { name: '验证并进入后台', exact: true }).click();
  await expect(page.getByRole('heading', { name: '企业管理', exact: true })).toBeVisible();
  await expect.poll(auth.adminReads).toBe(2);
});
