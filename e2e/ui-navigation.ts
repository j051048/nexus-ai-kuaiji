import { expect, type Page } from '@playwright/test';

export async function openNavigation(page: Page) {
  const opener = page.getByRole('button', { name: '打开工作空间导航', exact: true });
  if ((page.viewportSize()?.width ?? 1280) > 740) return;
  await expect(opener).toBeVisible();
  if ((await opener.getAttribute('aria-expanded')) !== 'true') await opener.click();
  await expect(page.getByRole('dialog', { name: '工作空间导航', exact: true })).toBeVisible({
    timeout: 30_000,
  });
}

export async function choosePersona(page: Page, persona: string) {
  await openNavigation(page);
  await page.getByLabel('切换验证身份').selectOption(persona);
  await expect(page.locator('.agent-sidebar, .client-portal')).toBeVisible({ timeout: 30_000 });
}

export async function openBusiness(page: Page) {
  const portal = page.getByRole('navigation', { name: '客户服务', exact: true });
  if (await portal.count()) {
    await portal.getByRole('button', { name: '提交', exact: true }).click();
    return;
  }
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: /^(业务事项|我的提交|部门事项)$/ })
    .click();
}

export async function openAccounting(page: Page) {
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '客户企业', exact: true })
    .click();
  await expect(page.getByLabel('搜索企业名称').first()).toBeVisible({ timeout: 30_000 });
}

async function expand(page: Page, label: string) {
  if (label === '会计工作区' || label === '处理记录与管理') await openNavigation(page);
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
  await expand(page, '处理记录与管理');
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
