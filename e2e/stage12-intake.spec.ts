import { expect, test } from '@playwright/test';
import { choosePersona, openBusiness } from './ui-navigation';

const receipt = {
  name: '第十二阶段原始票据.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};
const contract = {
  name: '采购说明.pdf',
  mimeType: 'application/pdf',
  buffer: Buffer.from(
    '%PDF-1.4\n% Stage 12 synthetic business evidence, no real customer data\n%%EOF\n',
  ),
};

test.setTimeout(90_000);

test('员工一体化原件上传、服务端草稿恢复和独立部门审批', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  const companyInput = page.getByLabel('事项客户企业');
  if (await companyInput.evaluate((node) => node.tagName === 'SELECT'))
    await companyInput.selectOption({ label: '青屿设计（虚构示例）' });
  else await expect(companyInput).toContainText('青屿设计（虚构示例）');
  await page.getByLabel('一体化录入上传附件').setInputFiles([receipt, contract]);
  await expect(page.getByText(receipt.name, { exact: true })).toBeVisible();
  await expect(page.getByText(contract.name, { exact: true })).toBeVisible();
  await page.getByLabel('事项摘要', { exact: true }).fill('第十二阶段采购报销草稿恢复');
  await page.getByLabel('事项金额', { exact: true }).fill('128.50');
  await page.getByLabel('交易对方', { exact: true }).fill('虚构票据供应商');
  await page.getByLabel('发生日期', { exact: true }).fill('2026-10-13');
  await page.getByLabel('事项分类', { exact: true }).selectOption('office');
  await page.getByRole('button', { name: /^采购 \/ 费用/ }).click();
  await expect(page.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
  await expect(page.getByRole('dialog').getByRole('status')).toContainText('草稿已保存');
  await expect(page.getByRole('dialog').getByRole('status')).toContainText('草稿已保存');
  await page.getByRole('button', { name: '关闭业务事项表单', exact: true }).click();
  await page.reload();
  // Demo reload intentionally signs in as the default accountant; authenticate
  // the same employee before testing its private server-side draft recovery.
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.getByText(/继续未完成的录入/).click();
  await page.getByRole('button', { name: /第十二阶段采购报销草稿恢复/ }).click();
  await expect(page.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
  await expect(page.getByText(receipt.name, { exact: true })).toBeVisible();
  await page
    .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
    .click();
  await page.screenshot({ path: 'artifacts/stage12-employee-intake.png', fullPage: true });
  await page.getByRole('dialog').getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: '第十二阶段采购报销草稿恢复', exact: true }),
  ).toBeVisible();
  // Reselecting an already active filter must keep the loaded list and detail.
  await page.getByLabel('筛选业务事项状态').selectOption('');
  await page.getByLabel('筛选业务事项企业').selectOption('');
  await expect(
    page
      .getByRole('button')
      .filter({ has: page.getByText('第十二阶段采购报销草稿恢复', { exact: true }) }),
  ).toBeVisible();
  await expect(
    page.getByRole('heading', { name: '第十二阶段采购报销草稿恢复', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '部门审批通过', exact: true })).toHaveCount(0);
  await choosePersona(page, 'department');
  await openBusiness(page);
  await expect(page.getByRole('tab', { name: '部门审批', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByLabel('筛选业务事项状态')).toHaveValue('submitted');
  await page
    .getByRole('button')
    .filter({ has: page.getByText('第十二阶段采购报销草稿恢复', { exact: true }) })
    .click();
  await expect(page.getByRole('button', { name: '部门审批通过', exact: true })).toBeEnabled();
});

test('手机录入同一界面完成并可返回独立事项列表', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.getByLabel('一体化录入上传附件').setInputFiles(receipt);
  await expect(page.getByText(receipt.name, { exact: true })).toBeVisible();
  await page.getByLabel('事项摘要', { exact: true }).fill('第十二阶段手机日常支出');
  await page.getByLabel('事项金额', { exact: true }).fill('18.60');
  await page.getByLabel('交易对方', { exact: true }).fill('虚构票据供应商');
  await page.getByRole('button', { name: /^日常收支/ }).click();
  await page
    .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
    .click();
  expect(
    await page.getByRole('dialog').evaluate((node) => node.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({ path: 'artifacts/stage12-mobile-intake.png', fullPage: false });
  await page.getByRole('dialog').getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: '第十二阶段手机日常支出', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '← 返回事项列表', exact: true }).click();
  await expect(page.getByText('第十二阶段手机日常支出', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
