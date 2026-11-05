import { choosePersona, openAccounting, openBusiness } from './ui-navigation';
import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const receipt = {
  name: 'MVP测试票据.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};

async function persona(page: Page, id: 'employee' | 'department' | 'finance' | 'accountant') {
  await choosePersona(page, id);
  if (id !== 'accountant') await openBusiness(page);
}

async function create(
  page: Page,
  description: string,
  template: '员工报销' | '采购 / 费用' | '日常收支',
  amount = '268.10',
) {
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.locator('.intake-template-picker > summary').click();
  await page.getByRole('button', { name: new RegExp('^' + template.replace('/', '\\/')) }).click();
  // A different book from the default accounting view exercises the draft jump.
  await page.getByLabel('事项客户企业').selectOption({ label: '青屿设计（虚构示例）' });
  await page.getByLabel('发生日期', { exact: true }).fill('2026-10-10');
  await page.getByLabel('事项金额', { exact: true }).fill(amount);
  await page.getByLabel('事项摘要', { exact: true }).fill(description);
  await page.getByLabel('交易对方', { exact: true }).fill('虚构票据供应商');
  await page.getByText('更多信息（分类、用途、项目）', { exact: true }).click();
  await page.getByLabel('事项分类', { exact: true }).selectOption('office');
  if (template === '日常收支')
    await page.getByLabel('收支方向', { exact: true }).selectOption('income');
  await page.getByLabel('业务用途', { exact: true }).fill('2–3 家企业试点流程验证');
  if (
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .count()
  )
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
  await page.getByRole('button', { name: '暂存草稿', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
  // Saving closes the dialog before the follow-up list refresh releases the
  // action lock. Match the visible upload control's real user readiness.
  await expect(page.getByRole('button', { name: '上传原始附件', exact: true })).toBeEnabled();
  await page.getByLabel('上传业务事项附件').setInputFiles(receipt);
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
}

async function open(page: Page, description: string, scope: '部门审批' | '企业财务' | '会计接收') {
  await page.getByRole('tab', { name: scope, exact: true }).click();
  if ((await page.getByLabel('筛选业务事项状态', { exact: true }).inputValue()) !== '')
    await page.getByLabel('筛选业务事项状态', { exact: true }).selectOption('');
  await page
    .getByRole('button')
    .filter({ has: page.getByText(description, { exact: true }) })
    .click();
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
}

test('员工报销经过独立审批与原件上传后交接指定账套、会计复核并导出', async ({ page }) => {
  // Four authenticated roles, attachment validation, review and an actual CSV download.
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await persona(page, 'employee');
  await expect(page.getByRole('button', { name: '会计工作台', exact: true })).toHaveCount(0);
  const description = '第三阶段MVP办公报销';
  await create(page, description, '员工报销');
  await page.screenshot({ path: 'artifacts/business-intake-review.png', fullPage: true });
  await page.getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('事项已提交');
  await expect(page.getByRole('button', { name: '部门审批通过', exact: true })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: '企业财务', exact: true })).toHaveCount(0);

  await persona(page, 'department');
  await open(page, description, '部门审批');
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载附件 ' + receipt.name, exact: true }).click();
  expect(await readFile((await (await downloaded).path())!)).toEqual(receipt.buffer);
  await page.getByRole('button', { name: '部门审批通过', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('部门审批已通过');

  await persona(page, 'finance');
  await open(page, description, '企业财务');
  await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('财务核验已通过');
  await page.getByLabel('交接到账套', { exact: true }).selectOption({ index: 1 });
  const targetBookName = await page
    .getByLabel('交接到账套', { exact: true })
    .locator('option:checked')
    .textContent();
  await page.getByRole('button', { name: '生成凭证草稿', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已交接并关联凭证草稿');
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'artifacts/business-workbench.png', fullPage: true });

  await persona(page, 'accountant');
  await openBusiness(page);
  await open(page, description, '会计接收');
  await page.getByRole('button', { name: '打开会计复核', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toHaveValue(description);
  await expect(drawer.locator('.inline-review-scope')).toContainText('青屿设计（虚构示例）');
  // The synthetic image has no OCR result. Follow the real manual exception
  // workflow instead of expecting an unrecognized attachment to pass review.
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重新核对', exact: true }).click();
  const missingOcr = page.locator('.intelligence-finding').filter({
    hasText: '附件尚未成功识别，需人工核对或重试',
  });
  await missingOcr
    .getByRole('textbox', { name: /^异常处理依据 / })
    .fill(
      '本地合成资料验收：已人工核对事项日期、金额、用途及附件；本票据为虚构流程测试，不代表真实票据验真。',
    );
  await missingOcr.getByRole('button', { name: '记录人工处理', exact: true }).click();
  await expect(missingOcr).toContainText('已人工处理');
  await page.getByRole('button', { name: '打开会计复核', exact: true }).click();
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await openAccounting(page);
  // Other cases create enough companies to paginate the list. Use the real
  // search control instead of assuming this target remains on the first page.
  const targetName = targetBookName!.split(' · ')[0];
  await page.getByRole('textbox', { name: '搜索企业名称', exact: true }).fill(targetName);
  await page.getByRole('listitem').filter({ hasText: targetName }).click();
  await page.getByLabel('选择凭证 ' + description, { exact: true }).check();
  const exportDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出凭证 CSV', exact: true }).click();
  const csv = await readFile((await (await exportDownload).path())!, 'utf8');
  expect(csv).toContain('2241');
  expect(csv).toContain('268.10');
  expect(csv).toContain(description);
});

test('采购费用退回重提保留版本和原件', async ({ page }) => {
  await page.goto('/');
  await persona(page, 'employee');
  const description = '第三阶段MVP采购补资料';
  await create(page, description, '采购 / 费用', '0.30');
  await page.getByRole('button', { name: '提交审批', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('事项已提交');
  await persona(page, 'department');
  await open(page, description, '部门审批');
  await page.getByLabel('退回原因', { exact: true }).fill('请补充采购用途');
  await page.getByRole('button', { name: /退回/ }).last().click();
  await expect(page.getByRole('status')).toContainText('事项已退回');
  await persona(page, 'employee');
  await page
    .getByRole('button')
    .filter({ has: page.getByText(description, { exact: true }) })
    .click();
  await expect(
    page.getByLabel('本次退回与补充说明').getByText('请补充采购用途', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: '修改并重新提交', exact: true }).click();
  await page.getByRole('button', { name: '修改信息', exact: true }).click();
  await page.getByLabel('业务用途', { exact: true }).fill('采购办公室耗材，已经补充确认');
  if (
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .count()
  )
    await page
      .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
      .click();
  await page.getByRole('button', { name: '修改后重新提交', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '修改业务事项' })).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('事项修改已保存并重新提交');
  await expect(page.getByText('采购办公室耗材，已经补充确认', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: '下载附件 ' + receipt.name, exact: true }),
  ).toBeVisible();
});

test('日常收支模板只录实际收付，窄屏业务列表和事项详情可读', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await persona(page, 'employee');
  await create(page, '第三阶段MVP日常收款', '日常收支', '3500.00');
  await expect(page.getByText('已收付', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(
    false,
  );
});
