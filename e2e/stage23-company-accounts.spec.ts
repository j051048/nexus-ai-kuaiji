import { expect, test, type APIRequestContext } from '@playwright/test';

async function createCompany(request: APIRequestContext) {
  const { token } = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${token}` };
  const response = await request.post('/api/companies', {
    headers,
    data: { name: `密码开户合成企业-${crypto.randomUUID().slice(0, 8)}`, tax_id: '' },
  });
  expect(response.status()).toBe(201);
  return { company: await response.json(), headers };
}

test('企业内直接开户明确员工范围，密码只用于本次请求，完成后刷新成员并可分发登录说明', async ({
  page,
  request,
  context,
}) => {
  const { company, headers } = await createCompany(request);
  const departmentResponse = await request.post(`/api/companies/${company.id}/departments`, {
    headers,
    data: { name: '合成销售部门' },
  });
  expect(departmentResponse.status()).toBe(201);
  const department = await departmentResponse.json();
  expect(typeof department.id).toBe('string');
  const secret = 'synthetic-initial-only-23';
  const member = {
    user_id: 'synthetic-password-employee',
    name: '虚构开户员工',
    email: 'password-employee@example.invalid',
    role: 'employee',
    department_id: department.id,
  };
  let created = false;
  const posts: Record<string, unknown>[] = [];
  await page.route(`**/api/companies/${company.id}/members`, (route) =>
    route.fulfill({ json: { members: created ? [member] : [] } }),
  );
  await page.route(`**/api/admin/companies/${company.id}`, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (created) {
      data.members = [member];
      data.company.member_count = 1;
    }
    await route.fulfill({ response, json: data });
  });
  await page.route(/\/api\/admin\/account-requests(?:\?|$)/, async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: { requests: [], provisioning_enabled: true } });
    posts.push(route.request().postDataJSON());
    created = true;
    return route.fulfill({
      status: 201,
      json: {
        request: {
          ...member,
          id: 'synthetic-password-request',
          company_role: member.role,
          status: 'completed',
          provisioning_mode: 'password',
        },
        password_already_created: false,
      },
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/admin/organizations/${company.id}`);
  await page.getByRole('button', { name: '开通账号', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '开通账号', exact: true });
  await expect(dialog.getByRole('radio', { name: /设置初始密码/ })).toBeChecked();
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('我的工作台');
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('默认只能查看本人事项');
  await dialog.getByLabel('权限角色', { exact: true }).selectOption('finance');
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('财务工作台');
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('另行分配');
  await dialog.getByLabel('权限角色', { exact: true }).selectOption('admin');
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('企业业务与管理工作空间');
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('不自动获得其他企业权限');
  await dialog.getByLabel('权限角色', { exact: true }).selectOption('employee');
  await dialog.getByLabel('姓名', { exact: true }).fill(member.name);
  await dialog.getByLabel('登录邮箱', { exact: true }).fill(member.email);
  await dialog.getByLabel('所属部门', { exact: true }).selectOption({ label: '合成销售部门' });
  await dialog.getByLabel('初始密码', { exact: true }).fill(secret);
  await dialog.getByLabel('再次输入初始密码', { exact: true }).fill('synthetic-wrong-confirmation');
  await dialog.getByRole('button', { name: '创建账号并授权', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('两次输入的初始密码不一致');
  expect(posts).toHaveLength(0);
  await dialog.getByLabel('再次输入初始密码', { exact: true }).fill(secret);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'artifacts/stage23-admin-password-mobile.png', fullPage: false });
  await dialog.getByRole('button', { name: '创建账号并授权', exact: true }).click();
  await expect(dialog.getByLabel('账号开通结果')).toContainText('员工现在可以使用');
  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({
    company_id: company.id,
    company_role: 'employee',
    department_id: department.id,
    provisioning_mode: 'password',
    initial_password: secret,
  });
  expect(posts[0].firm_role).toBeUndefined();
  await expect(dialog.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(secret);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain(secret);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await dialog.getByRole('button', { name: '复制登录说明（不含密码）', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '已复制登录说明', exact: true })).toBeVisible();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toContain(member.email);
  expect(clipboard).not.toContain(secret);
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await expect(page.locator('.admin-member').filter({ hasText: member.email })).toBeVisible();
  await page.getByRole('button', { name: '开通账号', exact: true }).click();
  await expect(dialog.getByLabel('初始密码', { exact: true })).toHaveValue('');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
});

test('密码开户失败需重新输入，响应丢失后的重试保留首次密码且不把新输入当作密码重设', async ({
  page,
  request,
}) => {
  const { company } = await createCompany(request);
  const record = {
    id: 'synthetic-password-retry',
    name: '虚构重试员工',
    email: 'password-retry@example.invalid',
    company_role: 'employee',
    status: 'failed',
    provisioning_mode: 'password',
  };
  const secret = 'synthetic-retry-never-reset';
  let attempts = 0;
  await page.route(/\/api\/admin\/account-requests(?:\/|\?|$)/, async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: { requests: [record], provisioning_enabled: true } });
    expect(route.request().url()).toContain('/synthetic-password-retry/retry');
    expect(route.request().postDataJSON()).toEqual({ initial_password: secret });
    attempts++;
    if (attempts === 1)
      return route.fulfill({ status: 503, json: { detail: '模拟返回结果暂时未知，请重试' } });
    record.status = 'completed';
    return route.fulfill({ json: { request: record, password_already_created: true } });
  });
  await page.goto(`/admin/organizations/${company.id}`);
  await page.getByRole('button', { name: '重试开通', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '重试密码开户', exact: true });
  await expect(dialog).toContainText('仍沿用首次创建时的密码');
  await dialog.getByLabel('初始密码', { exact: true }).fill(secret);
  await dialog.getByLabel('再次输入初始密码', { exact: true }).fill(secret);
  await dialog.getByRole('button', { name: '重试开通', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('结果暂时未知');
  await expect(dialog.getByLabel('初始密码', { exact: true })).toHaveValue('');
  await expect(dialog.getByLabel('再次输入初始密码', { exact: true })).toHaveValue('');
  await dialog.getByLabel('初始密码', { exact: true }).fill(secret);
  await dialog.getByLabel('再次输入初始密码', { exact: true }).fill(secret);
  await dialog.getByRole('button', { name: '重试开通', exact: true }).click();
  const completed = page.getByRole('dialog', { name: '账号授权已完成', exact: true });
  await expect(completed.getByLabel('账号开通结果')).toContainText(
    '仍使用首次设置的初始密码；本次输入未覆盖密码',
  );
  await expect(completed.locator('input[type="password"]')).toHaveCount(0);
  expect(attempts).toBe(2);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain(secret);
});

test('部门审批企业没有部门时阻止直接开户，明确引导先建立部门和审批人', async ({
  page,
  request,
}) => {
  const { company } = await createCompany(request);
  let writes = 0;
  await page.route(/\/api\/admin\/account-requests(?:\?|$)/, async (route) => {
    if (route.request().method() === 'GET')
      return route.fulfill({ json: { requests: [], provisioning_enabled: true } });
    writes++;
    return route.fulfill({ status: 500, json: { detail: '不应在缺少部门时发送开户请求' } });
  });
  await page.goto(`/admin/organizations/${company.id}`);
  await page.getByRole('button', { name: '开通账号', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '开通账号', exact: true });
  await dialog.getByLabel('姓名', { exact: true }).fill('虚构待配置员工');
  await dialog.getByLabel('登录邮箱', { exact: true }).fill('department-required@example.invalid');
  await dialog.getByLabel('初始密码', { exact: true }).fill('synthetic-department-only');
  await dialog.getByLabel('再次输入初始密码', { exact: true }).fill('synthetic-department-only');
  await expect(dialog).toContainText('此企业尚无部门');
  await expect(dialog.getByRole('button', { name: '创建账号并授权', exact: true })).toBeDisabled();
  await expect(dialog.getByLabel('登录后的权限预览')).toContainText('分配部门后：我的工作台');
  expect(writes).toBe(0);
});
