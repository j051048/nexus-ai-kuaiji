import { expect, type Page, type Response, type Locator } from '@playwright/test';
import { presentationRole, workSurface } from '../src/workspace-presentation';

/** Choose an explicit authorized company, never assume a multi-company default. */
export async function selectIntakeCompany(dialog: Locator, name = '青屿设计（虚构示例）') {
  const company = dialog.getByLabel('事项客户企业', { exact: true });
  await expect(company).toBeVisible();
  if (await company.evaluate((node) => node.tagName === 'SELECT')) {
    await company.selectOption({ label: name });
    await expect(company.locator('option:checked')).toHaveText(name);
  } else {
    await expect(company).toHaveText(name);
  }
}

export async function openNavigation(page: Page) {
  const opener = page.getByRole('button', { name: '打开工作空间导航', exact: true });
  if ((page.viewportSize()?.width ?? 1280) > 740) return;
  await expect
    .poll(
      async () => (await page.locator('.client-portal').isVisible()) || (await opener.isVisible()),
    )
    .toBe(true);
  if (await page.locator('.client-portal').isVisible()) return;
  await expect(opener).toBeVisible();
  if ((await opener.getAttribute('aria-expanded')) !== 'true') await opener.click();
  await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toBeVisible({
    timeout: 30_000,
  });
}

export async function choosePersona(page: Page, persona: string) {
  await openNavigation(page);
  const chooser = page.getByLabel('切换验证身份');
  await expect(chooser).toBeVisible();
  // A restored local persona does not trigger React onChange when reselected.
  if ((await chooser.inputValue()) === persona) {
    const close = page.getByRole('button', { name: '关闭导航', exact: true });
    if (await close.isVisible()) await close.click();
    return;
  }
  // A direct-page reload can briefly render a generic firm shell before the
  // newly signed-in employee's profile arrives. Observe that identity's real
  // profile response; do not add a hidden extra API read or guess from its name.
  const profiles: Response[] = [];
  const observe = (response: Response) => {
    if (
      ['/api/workspace/bootstrap', '/api/workspace/profile'].includes(
        new URL(response.url()).pathname,
      )
    )
      profiles.push(response);
  };
  page.on('response', observe);
  try {
    const [session] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === '/api/session' && url.searchParams.get('persona') === persona;
      }),
      page.getByLabel('切换验证身份').selectOption(persona),
    ]);
    expect(session.ok()).toBe(true);
    const { token } = await session.json();
    const selectedProfile = () =>
      profiles.find(
        (response) =>
          response.request().headers().authorization === `Bearer ${token}` && response.ok(),
      );
    await expect.poll(() => !!selectedProfile(), { timeout: 30_000 }).toBe(true);
    const result = await selectedProfile()!.json();
    const profile = result.profile ?? result;
    expect(typeof profile.can_accounting).toBe('boolean');
    const surface = workSurface(presentationRole(profile));
    await expect(
      page.locator(
        surface === 'client'
          ? '.client-portal'
          : surface === 'admin'
            ? '.admin-shell'
            : '.agent-sidebar',
      ),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel('切换验证身份')).toHaveValue(persona);
  } finally {
    page.off('response', observe);
  }
}

export async function openBusiness(page: Page) {
  await openNavigation(page);
  const portal = page.getByRole('navigation', { name: '客户服务', exact: true });
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  const labels = /^(业务事项|我的事项|部门事项|客户事项)$/;
  const action = nav.getByRole('button', { name: labels });
  const more = nav.locator('summary').filter({ hasText: /^日常业务$/ });
  await expect
    .poll(async () => (await portal.count()) + (await action.count()) + (await more.count()))
    .toBeGreaterThan(0);
  if (await portal.count()) {
    await portal.getByRole('button', { name: labels }).click();
    return;
  }
  if (
    !(await action.first().isVisible()) &&
    (await more.locator('..').getAttribute('open')) === null
  ) {
    await more.click();
  }
  await action.first().click();
}

export async function openAccounting(page: Page) {
  await openNavigation(page);
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  // 身份尚未返回时导航壳可能是空的：先等真实菜单项出现，再决定主入口。
  const primary = nav.getByRole('button', { name: /^(客户账套|客户企业)$/ });
  const professional = nav.getByRole('button', { name: '会计工作台', exact: true });
  const summary = nav.locator('summary').filter({ hasText: /^会计工作区$/ });
  await expect
    .poll(
      async () => (await primary.count()) + (await professional.count()) + (await summary.count()),
      {
        timeout: 30_000,
      },
    )
    .toBeGreaterThan(0);
  if (await primary.count()) {
    await primary.click();
  } else {
    if ((await summary.locator('..').getAttribute('open')) === null) await summary.click();
    await nav.getByRole('button', { name: '会计工作台', exact: true }).click();
  }
  await expect(page.getByLabel('搜索企业名称').first()).toBeVisible({ timeout: 30_000 });
}

/** The firm-owner primary menu has no home item; use its real brand link. */
export async function returnWorkspaceHome(page: Page) {
  await page.getByRole('link', { name: '衡账，返回工作台', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/home(?:[?#]|$)/);
}

/** Business shortcuts and technical settings keep their real authorized routes. */
export async function openFirmTool(page: Page, name: string) {
  await openNavigation(page);
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  const label =
    (
      { 事务所驾驶舱: '全所概览', 数据接入中心: '数据接入', 客户月度进度: '月度交付' } as Record<
        string,
        string
      >
    )[name] ?? name;
  const visible = nav.getByRole('button', { name: label, exact: true });
  if (await visible.count()) {
    await visible.click();
    return;
  }
  const technical = [
    '数据接入',
    '运行可观测性',
    '费用与运行监控',
    '后台运行',
    '票据处理任务',
    '票据核对异常',
    '试点评测',
  ].includes(label);
  const group = nav.locator('details').filter({
    has: page.locator('summary').filter({ hasText: technical ? /^设置$/ : /^日常业务$/ }),
  });
  await expect(group).toHaveCount(1);
  if ((await group.getAttribute('open')) === null) await group.locator('summary').click();
  const target = group.getByRole('button', { name: label, exact: true });
  await expect(target).toBeVisible();
  await target.click();
}

async function expand(page: Page, label: string) {
  if (label === '会计工作区' || label === '设置') await openNavigation(page);
  const summary = page.locator('summary').filter({ hasText: new RegExp(`^${label}$`) });
  // 本机 vite 首次编译可达数十秒；给首个导航断言充足时间，避免冷启动假失败。
  await expect(summary).toBeVisible({ timeout: 30_000 });
  if ((await summary.locator('..').getAttribute('open')) === null) await summary.click();
}

export async function openBookTools(page: Page) {
  await expand(page, '更多账套操作');
}
export async function openBookSettings(page: Page) {
  await expand(page, '账套设置');
}
export async function openManagement(page: Page) {
  // Client portal deliberately has no firm-management navigation.
  if (await page.getByRole('navigation', { name: '客户服务', exact: true }).count()) return;
  await expand(page, '设置');
}

export async function previewPastedStatement(
  page: Page,
  content: string,
  bank = '虚构银行-1234',
  namespace = 'bank-statement',
) {
  await page.getByRole('button', { name: '导入流水', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导入原始流水', exact: true });
  await dialog.locator('summary').filter({ hasText: '或直接粘贴中文' }).click();
  await dialog.getByLabel('粘贴流水内容（含表头）', { exact: true }).fill(content);
  await dialog.getByRole('button', { name: '识别粘贴内容', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '确认字段', exact: true })).toBeDisabled();
  await dialog.getByLabel('固定来源标识', { exact: true }).fill(namespace);
  await dialog.getByLabel('银行账号标识（脱敏）', { exact: true }).fill(bank);
  await dialog.getByRole('button', { name: '确认字段', exact: true }).click();
  await dialog.getByRole('button', { name: '预览流水', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '核对本次导入', exact: true })).toBeVisible();
  return dialog;
}

export async function confirmStatement(page: Page) {
  const dialog = page.getByRole('dialog', { name: '导入原始流水', exact: true });
  await dialog
    .getByLabel('我已核对目标企业、月份、账号、字段和收入支出合计', { exact: true })
    .check();
  await dialog.getByRole('button', { name: '确认导入', exact: true }).click();
  await expect(dialog).toHaveCount(0);
}
