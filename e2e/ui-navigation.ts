import { expect, type Page } from '@playwright/test';

export async function openAccounting(page: Page) {
  await expand(page, '会计工作区');
  await page.getByRole('navigation', { name: '会计协作', exact: true }).getByRole('button', { name: '会计工作台', exact: true }).click();
  await expect(page.getByLabel('搜索企业名称')).toBeVisible();
}

async function expand(page: Page, label: string) {
  const summary = page.locator('summary').filter({ hasText: new RegExp(`^${label}$`) });
  await expect(summary).toBeVisible();
  if (await summary.locator('..').getAttribute('open') === null) await summary.click();
}

export async function openBookTools(page: Page) { await expand(page, '更多账套操作'); }
export async function openManagement(page: Page) { await expand(page, '处理记录与管理'); }

export async function previewPastedStatement(page: Page, content: string, bank = '虚构银行-1234', namespace = 'bank-statement') {
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
  await dialog.getByLabel('我已核对目标企业、月份、账号、字段和收入支出合计', { exact: true }).check();
  await dialog.getByRole('button', { name: '确认导入', exact: true }).click();
  await expect(dialog).toHaveCount(0);
}
