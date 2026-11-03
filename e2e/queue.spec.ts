import { choosePersona, openBusiness, openManagement } from './ui-navigation';
import { expect, test, type Page } from '@playwright/test';

const original = {
  name: '第五阶段虚构票据.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};
async function createOriginal(page: Page, description: string) {
  await choosePersona(page, 'employee');
  await openBusiness(page);
  await page.getByRole('button', { name: '录入业务事项', exact: true }).click();
  await page.getByRole('button', { name: /^员工报销/ }).click();
  await page.getByLabel('事项客户企业').selectOption({ label: '青屿设计（虚构示例）' });
  await page.getByLabel('发生日期', { exact: true }).fill('2026-10-12');
  await page.getByLabel('事项金额', { exact: true }).fill('128.50');
  await page.getByLabel('事项摘要', { exact: true }).fill(description);
  await page.getByLabel('交易对方', { exact: true }).fill('第五阶段虚构供应商');
  await page.getByLabel('事项分类', { exact: true }).selectOption('office');
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
  // setInputFiles can target a disabled hidden input while saving still holds
  // the action lock; wait for the actual upload button to be usable first.
  await expect(page.getByRole('button', { name: '上传原始附件', exact: true })).toBeEnabled();
  const uploaded = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && response.url().includes('/attachments?'),
  );
  await page.getByLabel('上传业务事项附件').setInputFiles(original);
  const response = await uploaded;
  expect(response.status()).toBe(200);
  await expect(page.getByRole('button', { name: '提交审批', exact: true })).toBeEnabled();
  return response.json() as Promise<{
    id: string;
    company_id: string;
    version: number;
    attachments: { id: string }[];
  }>;
}

test('真实本地 UI：任务中心可读，供应商未配置时单份与批量识别都禁用', async ({ page }) => {
  await page.goto('/');
  const description = '第五阶段真实本地权限与等待配置验证';
  const event = await createOriginal(page, description);
  const panel = page.getByRole('region', { name: '票据识别与交叉核对' });
  await expect(panel.getByRole('button', { name: '识别票据', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '批量识别本页票据', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量识别本页票据', exact: true });
  await expect(dialog.getByText('等待管理员配置模型服务', { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: '提交 0 份识别任务', exact: true }),
  ).toBeDisabled();
  await expect(
    dialog.getByLabel(`识别附件 第五阶段虚构票据.png ${description}`, { exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByLabel('批量识别企业')).toHaveValue(event.company_id);
  await expect(dialog.getByLabel('批量识别企业').locator('option:not([value=""])')).toHaveCount(1);
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await choosePersona(page, 'accountant');
  await openManagement(page);
  await page.getByRole('button', { name: '票据处理任务', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '票据交给后台，判断留给会计。', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('当前没有可见处理任务', { exact: true })).toBeVisible();
  await expect(page.getByText('进度仅计入获授权条目', { exact: true })).toBeVisible();
});

test('显式合成队列响应仅验证展示：GET轮询、失败重试确认、取消依据和身份切换清空', async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  const description = '第五阶段合成队列界面验证（非真实模型处理）';
  const event = await createOriginal(page, description);
  const created = '2026-10-12T01:00:00Z';
  let detailReads = 0;
  let retries = 0;
  let cancellations = 0;
  let fixture = {
    id: 'synthetic-queue-rendering-only',
    company_id: event.company_id,
    company_name: '青屿设计（虚构示例）',
    created_at: created,
    status: 'running',
    progress: {
      total: 3,
      completed: 2,
      succeeded: 1,
      failed: 1,
      interrupted: 0,
      stale: 0,
      cancelled: 0,
      pending: 1,
      running: 0,
      percent: 66.67,
    },
    jobs: ['failed', 'queued', 'succeeded'].map((status, index) => ({
      id: `synthetic-ui-job-${index}`,
      batch_id: 'synthetic-queue-rendering-only',
      event_id: event.id,
      event_description: description,
      attachment_id: `${event.attachments[0].id}-${index}`,
      filename: `合成展示票据-${index}.png`,
      status,
      attempt_count: status === 'queued' ? 0 : 1,
      created_at: created,
      updated_at: created,
      error_code: status === 'failed' ? 'provider_temporary_error' : null,
      error_detail: status === 'failed' ? '合成测试：模型服务暂时不可用' : null,
      permissions: { retry: status === 'failed', cancel: status === 'queued' },
      attempts:
        status === 'queued'
          ? []
          : [
              {
                attempt_no: 1,
                status,
                phase: 'sent',
                created_at: created,
                finished_at: created,
                error_detail: status === 'failed' ? '合成测试：模型服务暂时不可用' : null,
              },
            ],
    })),
  };
  await page.route('**/api/intelligence/batches?**', async (route) => {
    const real = await route.fetch();
    expect(real.status()).toBe(200);
    await route.fulfill({
      response: real,
      json: { batches: [{ ...fixture, jobs: [] }], total: 1 },
    });
  });
  await page.route('**/api/intelligence/batches/synthetic-queue-rendering-only', async (route) => {
    expect(route.request().method()).toBe('GET');
    detailReads += 1;
    await route.fulfill({ status: 200, json: fixture });
  });
  await page.route('**/api/intelligence/jobs/synthetic-ui-job-0/retry', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON().idempotency_key.length).toBeGreaterThan(8);
    retries += 1;
    fixture = {
      ...fixture,
      progress: { ...fixture.progress, completed: 1, failed: 0, pending: 2, percent: 33.33 },
      jobs: fixture.jobs.map((job) =>
        job.id === 'synthetic-ui-job-0'
          ? {
              ...job,
              status: 'queued',
              error_code: null,
              error_detail: null,
              permissions: { retry: false, cancel: true },
            }
          : job,
      ),
    };
    await route.fulfill({ status: 200, json: fixture });
  });
  await page.route('**/api/intelligence/jobs/synthetic-ui-job-0/cancel', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON().reason).toBe('合成展示任务，不应发送真实供应商');
    cancellations += 1;
    fixture = {
      ...fixture,
      progress: { ...fixture.progress, completed: 2, pending: 1, cancelled: 1, percent: 66.67 },
      jobs: fixture.jobs.map((job) =>
        job.id === 'synthetic-ui-job-0'
          ? { ...job, status: 'cancelled', permissions: { retry: false, cancel: false } }
          : job,
      ),
    };
    await route.fulfill({ status: 200, json: fixture });
  });
  await choosePersona(page, 'accountant');
  await openManagement(page);
  await page.getByRole('button', { name: '票据处理任务', exact: true }).click();
  await page.getByRole('button', { name: /^查看识别批次 青屿设计（虚构示例）/ }).click();
  const detail = page.getByRole('complementary', { name: '票据处理任务详情', exact: true });
  await expect(
    detail.getByText('合成测试：模型服务暂时不可用', { exact: true }).first(),
  ).toBeVisible();
  await expect(detail.getByText('67%', { exact: true })).toBeVisible();
  await expect.poll(() => detailReads, { timeout: 8000 }).toBeGreaterThan(1);
  expect(retries).toBe(0);
  await detail.getByRole('button', { name: '重试此任务', exact: true }).click();
  await expect(detail.getByText(/中断或响应未确认的任务可能已产生供应商费用/)).toBeVisible();
  expect(retries).toBe(0);
  await detail.getByRole('button', { name: '确认重新排队', exact: true }).click();
  await expect.poll(() => retries).toBe(1);
  await expect(
    detail.getByText('失败条目已重新排队。后台会继续检查权限与当前附件版本。', { exact: true }),
  ).toBeVisible();
  await detail.getByRole('button', { name: '取消此任务', exact: true }).first().click();
  await expect(detail.getByRole('button', { name: '确认取消任务', exact: true })).toBeDisabled();
  await detail.getByLabel('取消识别任务依据').fill('合成展示任务，不应发送真实供应商');
  await detail.getByRole('button', { name: '确认取消任务', exact: true }).click();
  await expect.poll(() => cancellations).toBe(1);
  await expect(detail.getByText('已取消', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/queue-synthetic-task-ui.png', fullPage: true });
  await page.unroute('**/api/intelligence/batches?**');
  await page.unroute('**/api/intelligence/batches/synthetic-queue-rendering-only');
  await choosePersona(page, 'employee');
  await expect(page.getByRole('complementary', { name: '票据处理任务详情' })).toHaveCount(0);
  await choosePersona(page, 'accountant');
  await openManagement(page);
  await page.getByRole('button', { name: '票据处理任务', exact: true }).click();
  // Other tests may have retained authorized real XML jobs. Identity change
  // must clear this synthetic batch and its selection, not hide valid jobs.
  await expect(page.getByText('合成展示票据-0.png', { exact: true })).toHaveCount(0);
  await expect(detail.getByText(description, { exact: true })).toHaveCount(0);
  await expect(page.locator('.queue-batch-row.selected')).toHaveCount(0);
  await expect(detail.getByText('选择一个处理批次', { exact: true })).toBeVisible();
  await expect(detail.locator('.queue-job')).toHaveCount(0);
  await expect(page).not.toHaveURL(/synthetic-queue-rendering-only/);
});

test('显式合成入队回执：批量选择保留版本，响应丢失后复用编号并可离开页面', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/');
  const description = '第五阶段合成入队回执验证（非真实模型处理）';
  const event = await createOriginal(page, description);
  await expect(page.getByRole('button', { name: '批量识别本页票据', exact: true })).toBeVisible();
  const created = '2026-10-12T02:00:00Z';
  const fixture = {
    id: 'synthetic-enqueue-receipt-only',
    company_id: event.company_id,
    company_name: '青屿设计（虚构示例）',
    created_at: created,
    status: 'queued',
    progress: {
      total: 1,
      completed: 0,
      succeeded: 0,
      failed: 0,
      interrupted: 0,
      stale: 0,
      cancelled: 0,
      pending: 1,
      running: 0,
      percent: 0,
    },
    jobs: [
      {
        id: 'synthetic-enqueue-job',
        batch_id: 'synthetic-enqueue-receipt-only',
        event_id: event.id,
        event_description: description,
        attachment_id: event.attachments[0].id,
        filename: '第五阶段虚构票据.png',
        status: 'queued',
        attempt_count: 0,
        error_code: null,
        error_detail: null,
        created_at: created,
        updated_at: created,
        permissions: { retry: false, cancel: true },
        attempts: [],
      },
    ],
  };
  const keys: string[] = [];
  let synchronousOcrCalls = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/ocr$/.test(request.url())) synchronousOcrCalls += 1;
  });
  await page.route('**/api/intelligence/config', async (route) => {
    const real = await route.fetch();
    expect(real.status()).toBe(200);
    await route.fulfill({
      response: real,
      json: {
        ...(await real.json()),
        configured: true,
        vision_ready: true,
        enabled_company_ids: [event.company_id],
        provider_label: '合成界面配置（不调用模型）',
      },
    });
  });
  await page.route('**/api/intelligence/batches', async (route) => {
    expect(route.request().method()).toBe('POST');
    const body = route.request().postDataJSON();
    expect(body.company_id).toBe(event.company_id);
    expect(body.items).toEqual([
      {
        event_id: event.id,
        attachment_id: event.attachments[0].id,
        expected_version: event.version,
      },
    ]);
    keys.push(body.idempotency_key);
    // Simulate an unknown acknowledgement, without submitting to a real worker or supplier.
    if (keys.length === 1) await route.abort('connectionreset');
    else await route.fulfill({ status: 202, json: fixture });
  });
  await page.route('**/api/intelligence/batches?**', async (route) => {
    const real = await route.fetch();
    await route.fulfill({
      response: real,
      json: {
        batches: keys.length > 1 ? [{ ...fixture, jobs: [] }] : [],
        total: keys.length > 1 ? 1 : 0,
      },
    });
  });
  await page.route('**/api/intelligence/batches/synthetic-enqueue-receipt-only', async (route) => {
    await route.fulfill({ status: 200, json: fixture });
  });
  await page.getByRole('button', { name: '批量识别本页票据', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量识别本页票据', exact: true });
  await dialog.getByLabel(`识别附件 第五阶段虚构票据.png ${description}`, { exact: true }).check();
  await dialog.getByRole('button', { name: '提交 1 份识别任务', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('保持当前选择再次提交将复用请求编号');
  await dialog.getByRole('button', { name: '提交 1 份识别任务', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  // The employee can submit their own originals; technical task inspection is
  // a separate authorized accountant surface, not an employee navigation item.
  await choosePersona(page, 'accountant');
  await openManagement(page);
  await page.getByRole('button', { name: '票据处理任务', exact: true }).click();
  await page.getByRole('button', { name: /^查看识别批次 青屿设计（虚构示例）/ }).click();
  await expect(
    page
      .getByRole('complementary', { name: '票据处理任务详情', exact: true })
      .getByText('第五阶段虚构票据.png', { exact: true }),
  ).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(synchronousOcrCalls).toBe(0);
  await openBusiness(page);
  await expect(
    page.getByRole('heading', { name: '从业务发生，到有据可记。', exact: true }),
  ).toBeVisible();
  await openManagement(page);
  await expect(page.getByRole('button', { name: '票据处理任务', exact: true })).toBeEnabled();
});
