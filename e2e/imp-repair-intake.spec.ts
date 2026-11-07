import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { choosePersona, openBusiness } from './ui-navigation';

test.setTimeout(90000);
async function open(page: Page, request: APIRequestContext, title: string) {
  const session = await (await request.get('/api/session?persona=employee')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const context = await (await request.get('/api/business/context', { headers })).json();
  const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
  await page.goto('/workspace/home');
  await choosePersona(page, 'employee');
  await page.getByRole('button', { name: /^提交报销/ }).click();
  const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
  const select = dialog.getByRole('combobox', { name: '事项客户企业' });
  await expect(select).toBeVisible();
  await select.selectOption(company.id);
  const department = dialog.getByRole('combobox', { name: '事项所属部门' });
  if (await department.count())
    await department.selectOption(company.department_id ?? company.departments[0].id);
  await dialog.getByLabel('发生日期', { exact: true }).fill('2026-10-03');
  await dialog.getByLabel('事项金额', { exact: true }).fill('128.50');
  await dialog.getByLabel('事项摘要', { exact: true }).fill(title);
  await dialog.getByLabel('交易对方', { exact: true }).fill('虚构测试商户');
  return { dialog, headers, company };
}
const file = {
  name: '隔离原件.pdf',
  mimeType: 'application/pdf',
  buffer: Buffer.from('%PDF-1.4\n% synthetic only\n%%EOF\n'),
};
for (const width of [1366, 390]) {
  test(`IMP-R4 upload response loss and refreshed draft ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    const title = '恢复-' + crypto.randomUUID();
    const { dialog, headers, company } = await open(page, request, title);
    let draftId = '';
    let uploads = 0;
    await page.route(/\/api\/intake\/drafts\/[^/]+\/attachments\?/, async (route) => {
      uploads++;
      draftId = new URL(route.request().url()).pathname.split('/')[4];
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      await route.abort('failed');
    });
    await dialog.getByLabel('一体化录入上传附件').setInputFiles(file);
    await expect(dialog.getByRole('alert')).toContainText('读取最新草稿');
    await dialog.getByRole('button', { name: '读取最新草稿', exact: true }).click();
    await expect(dialog.getByText(file.name, { exact: true })).toBeVisible();
    const saved = await (await request.get(`/api/intake/drafts/${draftId}`, { headers })).json();
    expect(saved.attachments).toHaveLength(1);
    expect(saved.company_id).toBe(company.id);
    await page.reload();
    await choosePersona(page, 'employee');
    await openBusiness(page);
    await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
    await dialog
      .locator('summary')
      .filter({ hasText: /继续未完成的录入/ })
      .click();
    await dialog.getByRole('button').filter({ hasText: title }).click();
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
    await expect(dialog.getByLabel('发生日期', { exact: true })).toHaveValue('2026-10-03');
    await expect(dialog.getByText(file.name, { exact: true })).toBeVisible();
    expect(uploads).toBe(1);
    await page.screenshot({ path: test.info().outputPath('draft-restored.png'), fullPage: true });
  });
  test(`IMP-R4 unknown submit queries original result ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    const { dialog, headers } = await open(page, request, '未知提交-' + crypto.randomUUID());
    await dialog.getByLabel('一体化录入上传附件').setInputFiles(file);
    await expect(dialog.getByText(file.name, { exact: true })).toBeVisible();
    let writes = 0;
    let eventId = '';
    await page.route(/\/api\/intake\/drafts\/[^/]+\/complete$/, async (route) => {
      writes++;
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      const body = await response.json();
      eventId = body.event?.id ?? body.id;
      await route.abort('failed');
    });
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: '提交审批', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('读取最新草稿');
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    await page.screenshot({ path: test.info().outputPath('unknown-submit.png'), fullPage: true });
    await dialog.getByRole('button', { name: '读取最新草稿', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(writes).toBe(1);
    const saved = await (await request.get(`/api/business/events/${eventId}`, { headers })).json();
    expect(saved.status).toBe('submitted');
    expect(saved.attachments).toHaveLength(1);
  });
}
test('IMP-R4 200 percent layout zoom retains keyboard operation', async ({ page, request }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.addInitScript(() =>
    document.addEventListener('DOMContentLoaded', () => {
      document.documentElement.style.zoom = '2';
    }),
  );
  const { dialog } = await open(page, request, '真实布局缩放');
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).zoom)).toBe('2');
  await dialog.getByLabel('事项摘要', { exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(dialog.getByLabel('交易对方', { exact: true })).toBeFocused();
  await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: test.info().outputPath('layout-zoom-200.png'), fullPage: true });
});
