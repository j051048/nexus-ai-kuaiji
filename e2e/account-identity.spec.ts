import { expect, test, type Page } from '@playwright/test';

test.setTimeout(90_000);
test.afterEach(async ({ page }) => {
  // Drain intercepted reads before Playwright closes their request context.
  await page.unrouteAll({ behavior: 'wait' });
});
const identity = (page: Page) => page.getByRole('region', { name: '当前登录身份', exact: true });

async function usePersona(page: Page, persona: string) {
  await page.unroute(/\/api\/session(?:\?|$)/);
  await page.route(/\/api\/session(?:\?|$)/, async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    const response = await route.fetch({ url: url.toString() });
    await route.fulfill({ response });
  });
}

for (const [device, width] of [
  ['desktop', 1280],
  ['mobile', 375],
] as const) {
  test(`${device} 已登录账号常显真实岗位、姓名和可展开范围，无需打开侧栏`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const [persona, role] of [
      ['accountant', '事务所负责人'],
      ['employee', '企业员工'],
      ['department', '部门审批人'],
      ['finance', '企业财务'],
    ]) {
      await usePersona(page, persona);
      const bootstrap = page.waitForResponse(
        (response) => response.url().includes('/api/workspace/bootstrap') && response.ok(),
      );
      await page.goto('/workspace/home');
      const current = (await (await bootstrap).json()).me;
      await expect(
        page.locator('.workspace-home,.client-portal,.agent-shell').first(),
      ).toBeVisible();
      const region = identity(page);
      await expect(region).toBeVisible();
      await expect(region.locator('.account-identity-role')).toHaveText(role);
      await expect(region.locator('.account-identity-name')).not.toBeEmpty();
      const unit =
        persona === 'accountant'
          ? current.firms.find((firm: { id: string }) => firm.id === current.current_firm_id).name
          : current.company_memberships
              .slice(0, 2)
              .map((company: { name: string }) => company.name)
              .join('、') +
            (current.company_memberships.length > 2
              ? `等 ${current.company_memberships.length} 家企业`
              : '');
      expect(unit).not.toBe('');
      await expect(region.locator('.account-identity-unit-name')).toHaveText(unit);
      await expect(region.locator('.account-identity-unit-name')).toBeVisible();
      await expect(region.locator('details')).not.toHaveAttribute('open');
      await expect(region.locator('summary')).toBeInViewport();
      await page.screenshot({ path: `tmp_test/account-identity/${device}-${persona}-closed.png` });
      await region.locator('summary').focus();
      await page.keyboard.press('Enter');
      await expect(region.getByText('授权范围', { exact: true })).toBeVisible();
      for (const company of current.company_memberships) {
        await expect(region.locator('dd').filter({ hasText: company.name })).toHaveCount(1);
      }
      await expect(region).not.toContainText('超级管理员');
      if (persona !== 'accountant') await expect(region).not.toContainText('本事务所全部企业');
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      ).toBe(true);
      await page.screenshot({ path: `tmp_test/account-identity/${device}-${persona}.png` });
    }
    await page
      .getByRole('navigation', { name: '客户服务' })
      .getByRole('button', { name: '业务事项', exact: true })
      .click();
    await expect(identity(page).locator('.account-identity-role')).toHaveText('企业财务');
  });

  test(`${device} 管理后台常显账号与事务所管理身份`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await usePersona(page, 'accountant');
    await page.goto('/admin');
    await expect(page.getByRole('navigation', { name: '管理后台导航' })).toBeVisible();
    const region = identity(page);
    await expect(region.locator('.account-identity-role')).toHaveText('事务所负责人');
    await expect(region.locator('.account-identity-unit-name')).toHaveText(
      await page.locator('.admin-firm-name').innerText(),
    );
    await expect(region.locator('summary')).toBeInViewport();
    await region.locator('summary').click();
    await expect(region).toContainText('本事务所全部企业（不含其他事务所）');
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
    await page.screenshot({ path: `tmp_test/account-identity/${device}-admin.png` });
  });
}

test('兼任复核不改主岗位，权限拒绝后清除旧能力，身份详情不发送业务请求', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await usePersona(page, 'finance');
  // Presentation-only projection; actual API authorization is unchanged.
  await page.route(/\/api\/workspace\/(?:bootstrap|profile)(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const profile = {
      ...(body.profile ?? body),
      display_role: 'finance',
      can_accounting: true,
      can_review: true,
    };
    await route.fulfill({ response, json: body.profile ? { ...body, profile } : profile });
  });
  await page.goto('/workspace/home');
  const region = identity(page);
  await expect(region.locator('.account-identity-role')).toHaveText('企业财务');
  const writes: string[] = [];
  page.on('request', (request) => {
    if (
      new URL(request.url()).pathname.startsWith('/api/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())
    )
      writes.push(request.method());
  });
  await region.locator('summary').click();
  await expect(region).toContainText('复核获授权账套');
  await expect(region).not.toContainText('本事务所全部企业');
  await region.locator('summary').click();
  await region.locator('summary').click();
  expect(writes).toEqual([]);
  await page.route(/\/api\/workspace\/tasks(?:\?|$)/, (route) =>
    route.fulfill({ status: 403, json: { detail: 'synthetic forbidden' } }),
  );
  await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
  await expect(region.locator('.account-identity-role')).toHaveText('权限待重新确认');
  await expect(region.locator('.account-identity-unit-name')).toHaveText('所属单位待确认');
  await expect(region.getByText('企业成员身份', { exact: true })).toHaveCount(0);
  await expect(region).not.toContainText('复核获授权账套');
  await expect(page).not.toHaveURL(/login/);
});

test('手机长账号名不截断身份，权限详情保持可读字号', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await usePersona(page, 'employee');
  const name = '虚构企业用于验收的超长员工姓名'.repeat(4);
  const companyName = '虚构跨区域供应链综合管理服务有限公司（南方分部）';
  await page.route(/\/api\/(?:me|workspace\/bootstrap)(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const me = body.me ?? body;
    const changed = {
      ...me,
      user: { ...me.user, name },
      company_memberships: [
        { id: 'unit-a', name: companyName, role: 'employee' },
        { id: 'unit-b', name: '虚构第二所属企业', role: 'finance' },
      ],
    };
    await route.fulfill({ response, json: body.me ? { ...body, me: changed } : changed });
  });
  await page.goto('/workspace/home');
  const region = identity(page);
  await expect(region.locator('.account-identity-name')).toHaveText(name);
  await expect(region.locator('.account-identity-unit-name')).toHaveText(
    companyName + '、虚构第二所属企业',
  );
  await region.locator('summary').click();
  await expect(region.getByText(companyName + ' · 企业员工', { exact: true })).toBeVisible();
  await expect(region.getByText('虚构第二所属企业 · 企业财务', { exact: true })).toBeVisible();
  expect(
    await region
      .locator('dd')
      .first()
      .evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize)),
  ).toBeGreaterThanOrEqual(13);
  expect(
    await region
      .locator('dt')
      .first()
      .evaluate((el) => {
        const style = getComputedStyle(el);
        return el.getBoundingClientRect().height <= Number.parseFloat(style.lineHeight) + 1;
      }),
  ).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: 'tmp_test/account-identity/mobile-long-name.png' });
});
