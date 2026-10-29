import { expect, test, type Page } from '@playwright/test';
import type { MonthlyPackage } from '../src/monthly-package-types';

const hash = 'a'.repeat(64);
const pid = '11111111111111111111111111111111';
function pack(book = 'book-a', patch: Partial<MonthlyPackage> = {}): MonthlyPackage {
  return {
    id: pid,
    version: 1,
    book_id: book,
    company_id: book === 'book-a' ? 'company-a' : 'company-b',
    period: '2026-10',
    report_id: 'report',
    report_version: 1,
    manifest_sha256: hash,
    created_at: '2026-10-29',
    created_by: 'accountant',
    status: 'frozen',
    contact_user_id: 'client',
    superseded: false,
    can_open: true,
    can_acknowledge: true,
    customer_opening_status: 'not_opened',
    customer_receipt_status: 'not_recorded',
    external_import_status: 'separate_workflow',
    opening: null,
    acknowledgement: null,
    ...patch,
  };
}
function overview(book = 'book-a') {
  const item = pack(book);
  return {
    book_id: book,
    company_id: item.company_id,
    period: item.period,
    ready: true,
    blockers: [],
    warnings: [],
    reports: [],
    permissions: { generate: false },
    contact: { user_id: 'client', eligible: true, status: 'configured' },
    packages: [item],
  };
}
async function mount(
  page: Page,
  options: {
    opening?: Partial<MonthlyPackage>;
    ack?: Partial<MonthlyPackage>;
    openStatus?: number;
    ackStatus?: number;
    code?: string;
    delayed?: boolean;
    search?: string;
    versions?: boolean;
  } = {},
) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  let release: (() => void) | undefined;
  await page.route('**/api/**', async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    calls.push({ path, method: request.method(), body: request.postDataJSON() });
    const book = path.includes('/book-b/') ? 'book-b' : 'book-a';
    if (path.endsWith('/monthly-package')) {
      const data = overview(book);
      if (options.versions)
        data.packages = [
          pack(book, { superseded: true }),
          pack(book, { id: '22222222222222222222222222222222', version: 2 }),
        ];
      return route.fulfill({ json: data });
    }
    if (path.endsWith('/open')) {
      if (options.delayed && book === 'book-a')
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (options.openStatus)
        return route.fulfill({
          status: options.openStatus,
          json: { detail: 'internal private error', code: options.code },
        });
      return route.fulfill({
        json: pack(book, {
          customer_opening_status: 'opened',
          opening: { opened_at: '2026-10-29', opened_by: 'client' },
          files: [{ path: book + '.csv', size_bytes: 128, sha256: hash }],
          file_count: 1,
          ...(options.versions && path.includes('22222222222222222222222222222222')
            ? { id: '22222222222222222222222222222222', version: 2 }
            : {}),
          ...(options.versions && path.includes(pid) ? { superseded: true } : {}),
          ...options.opening,
        }),
      });
    }
    if (path.endsWith('/acknowledge')) {
      if (options.ackStatus)
        return route.fulfill({
          status: options.ackStatus,
          json: { detail: 'internal private error', code: options.code },
        });
      return route.fulfill({
        json: pack(book, {
          customer_receipt_status: 'acknowledged',
          acknowledgement: {
            acknowledged_at: '2026-10-29',
            acknowledged_by: 'client',
            reason: '收到',
          },
          ...options.ack,
        }),
      });
    }
    return route.fulfill({ json: { package: pack(book) } });
  });
  await page.goto('/e2e/fixtures/n04-package-harness.html' + (options.search ?? ''));
  await expect(page.getByRole('heading', { name: '月度交付包', exact: true })).toBeVisible();
  return { calls, release: () => release?.() };
}
async function confirm(page: Page) {
  await page.getByLabel('签收说明', { exact: true }).fill('已核对本月冻结资料包');
  await page.getByRole('checkbox', { name: '我已核对月份与版本，并确认收到本版全部文件' }).check();
  await page.getByRole('button', { name: '确认收到第 1 版', exact: true }).click();
}

for (const viewport of [
  { name: '桌面', width: 1280, height: 900 },
  { name: '手机', width: 390, height: 844 },
]) {
  test(`N04 ${viewport.name}拒绝同ID错manifest打开结果，清单与状态原子接纳`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await mount(page, { opening: { manifest_sha256: 'b'.repeat(64) } });
    await expect(page.getByRole('alert')).toContainText('版本');
    await expect(page.getByText('客户已查看 · 待签收', { exact: true })).toHaveCount(0);
    await expect(page.getByText('本版包含 1 个文件', { exact: true })).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath(`n04-${viewport.name}-version-error.png`),
      fullPage: true,
    });
  });
}
test('N04 签收响应必须匹配请求的包ID版本摘要', async ({ page }) => {
  await mount(page, { ack: { id: '22222222222222222222222222222222', version: 2 } });
  await confirm(page);
  await expect(page.getByRole('alert')).toContainText('签收结果');
  await expect(page.getByText('本版本客户签收已留痕。')).toHaveCount(0);
});
test('N04 403 撤权清空原件清单和签收状态，重查不复用旧确认', async ({ page }) => {
  const { calls } = await mount(page);
  await expect(page.getByText('本版包含 1 个文件', { exact: true })).toBeVisible();
  await page.getByLabel('签收说明', { exact: true }).fill('旧确认不许复用');
  await page.getByRole('checkbox').check();
  await page.evaluate(() =>
    window.dispatchEvent(new CustomEvent('accounting:forbidden', { detail: 'n04-local-only' })),
  );
  await expect(page.getByText('本版包含 1 个文件', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toHaveCount(0);
  await page.getByRole('button', { name: '重新检查交付条件' }).click();
  await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toBeDisabled();
  expect(calls.filter((x) => x.path.endsWith('/acknowledge'))).toHaveLength(0);
});
test('N04 切换企业后迟到打开结果不能复活旧企业数据', async ({ page }) => {
  const control = await mount(page, { delayed: true });
  await expect.poll(() => control.calls.filter((c) => c.path.endsWith('/open')).length).toBe(1);
  await page.getByRole('button', { name: '切换虚构企业' }).click();
  await expect(page.getByText('本版包含 1 个文件', { exact: true })).toBeVisible();
  control.release();
  await page.getByText('本版包含 1 个文件', { exact: true }).click();
  await expect(page.getByText('book-b.csv', { exact: true })).toBeVisible();
  await expect(page.getByText('book-a.csv', { exact: true })).toHaveCount(0);
});
for (const status of [401, 403, 409, 503]) {
  test(`N04 签收失败 ${status} 只允许重新读取，不自动重签或泄漏内部错误`, async ({ page }) => {
    const { calls } = await mount(page, { ackStatus: status });
    await confirm(page);
    await expect(page.getByRole('alert')).not.toContainText('internal private error');
    await expect(page.getByRole('alert')).toContainText(
      status === 401 ? '登录' : status === 403 ? '授权' : status === 409 ? '版本' : '结果尚未确认',
    );
    await page.getByRole('button', { name: '重新检查交付条件' }).click();
    await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toBeDisabled();
    expect(calls.filter((x) => x.path.endsWith('/acknowledge'))).toHaveLength(1);
  });
}

test('N04 MFA 保持二次验证语义，不误报登录失效或自动重签', async ({ page }) => {
  const { calls } = await mount(page, { ackStatus: 403, code: 'admin_mfa_required' });
  await confirm(page);
  await expect(page.getByRole('alert')).toContainText('二次验证');
  await expect(page.getByRole('alert')).not.toContainText('登录已失效');
  await page.getByRole('button', { name: '重新检查交付条件' }).click();
  await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toBeDisabled();
  expect(calls.filter((x) => x.path.endsWith('/acknowledge'))).toHaveLength(1);
});

for (const search of [
  '?package=',
  '?package=invalid',
  '?package=aaaaaaaa',
  `?package=${pid}&package=aaaaaaaa`,
]) {
  test(`N04 无效或缺失准确版本不回落最新可签收包 ${search}`, async ({ page }) => {
    const { calls } = await mount(page, { search });
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByRole('button', { name: /确认收到第|下载第/ })).toHaveCount(0);
    await expect(page.getByLabel('签收说明', { exact: true })).toHaveCount(0);
    expect(
      calls.filter((x) => x.path.endsWith('/open') || x.path.endsWith('/acknowledge')),
    ).toHaveLength(0);
  });
}

test('N04 历史版本链接刷新、前进后退保持准确包且不携带确认', async ({ page }) => {
  await mount(page, { search: `?package=${pid}`, versions: true });
  await expect(page.getByText('本月资料包（第 1 版）', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认收到第 2 版' })).toHaveCount(0);
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`package=${pid}`));
  await page.getByLabel('签收说明', { exact: true }).fill('旧版确认不可带到下一版');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '打开第二版链接' }).click();
  await expect(page.getByRole('button', { name: '确认收到第 2 版' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole('button', { name: '确认收到第 1 版' })).toBeDisabled();
  await page.goForward();
  await expect(page.getByRole('button', { name: '确认收到第 2 版' })).toBeDisabled();
});
