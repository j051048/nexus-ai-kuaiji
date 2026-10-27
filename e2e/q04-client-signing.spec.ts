import { expect, test, type Page } from '@playwright/test';
import { checked, monthlyFixture } from './formal-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 20_000 });

const viewports = [
  { name: '桌面', width: 1280, height: 900 },
  { name: '手机', width: 390, height: 844 },
];

async function clientSession(page: Page) {
  // Local demonstration sessions reset to accountant on reload; pin the real session
  // endpoint instead of faking claims in the browser.
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'finance');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

for (const viewport of viewports) {
  test(`客户在${viewport.name}上查看资料包内容、留下查看痕迹并签收`, async ({
    page,
    request,
  }, testInfo) => {
    const f = await monthlyFixture(request);
    const frozen = await checked(
      await request.post(`/api/books/${f.book.id}/delivery/monthly-package`, {
        headers: f.headers,
        data: {
          report_id: f.report.report.id,
          expected_report_version: f.report.report.version,
          reason: '真实接口冻结本月零业务交付包，供客户端签收验收',
          idempotency_key: `q04-freeze-${crypto.randomUUID()}`,
        },
      }),
    );
    expect(frozen.contact_user_id).toBe('local-business-finance');
    // The generation response speaks to the generator; the contact's view is separate.
    expect(frozen.can_open).toBe(false);
    const clientView = await checked(
      await request.get(`/api/books/${f.book.id}/delivery/monthly-package/${frozen.id}`, {
        headers: f.finance,
      }),
      200,
    );
    expect(clientView.package.can_open).toBe(true);
    expect(clientView.package.customer_opening_status).toBe('not_opened');
    expect(clientView.package.file_count).toBe(11);
    expect(clientView.package.files[0].sha256).toMatch(/^[a-f0-9]{64}$/);

    // The firm feed must describe this month before it is signed: a frozen package
    // with no signature and no configured recipient, owned by the firm.
    const pending = await checked(
      await request.get('/api/delivery/followups?limit=50', { headers: f.headers }),
      200,
    );
    const before = pending.items.find(
      (item: { book_id: string; period: string }) =>
        item.book_id === f.book.id && item.period === '2026-10',
    );
    expect(before).toBeTruthy();
    expect(before.package_id).toBe(frozen.id);
    expect(before.reasons).toContain('signature_missing');
    expect(before.owner).toBe('firm_manager');

    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await clientSession(page);
    await page.goto(`/workspace/briefing?book=${f.book.id}&company=${f.company.id}&period=2026-10`);
    const panel = page.getByRole('region', { name: '月度交付包', exact: true });
    await expect(panel.getByRole('heading', { name: '月度交付包', exact: true })).toBeVisible({
      timeout: 30_000,
    });
    // The client never sees the firm's generation form or internal blockers.
    await expect(panel.getByRole('button', { name: '生成本月交付包', exact: true })).toHaveCount(0);
    await expect(panel.getByText('交付前还需处理', { exact: true })).toHaveCount(0);

    // Contents are listed, not just a download button.
    const contents = panel.locator('summary').filter({ hasText: '本版包含 11 个文件' });
    await expect(contents).toBeVisible();
    await contents.click();
    await expect(panel.getByText('README.html', { exact: true })).toBeVisible();
    await expect(panel.getByText('report.html', { exact: true })).toBeVisible();
    await expect(panel.getByText('account_balances.csv', { exact: true })).toBeVisible();
    // 每个文件都要给出可读体积：清单字段是 size_bytes，写成 bytes 会渲染成 NaN。
    const sizes = panel.locator('.monthly-package-contents li span');
    await expect(sizes).toHaveCount(11);
    for (const text of await sizes.allTextContents()) {
      expect(text).toMatch(/^\d+(\.\d+)?\s(B|KB|MB)$/);
    }
    await expect(panel.getByText('NaN', { exact: false })).toHaveCount(0);

    // Opening the page is recorded as a view, never as a receipt.
    await expect
      .poll(
        async () => {
          const detail = await checked(
            await request.get(`/api/books/${f.book.id}/delivery/monthly-package/${frozen.id}`, {
              headers: f.finance,
            }),
            200,
          );
          return detail.package.customer_opening_status;
        },
        { timeout: 20_000 },
      )
      .toBe('opened');
    await expect(panel.getByText(/待客户查看|客户已查看 · 待签收/, { exact: true })).toBeVisible();
    expect(
      (
        await request.get(
          `/api/books/${f.book.id}/delivery/monthly-package/${frozen.id}/download`,
          { headers: f.headers },
        )
      ).status(),
    ).toBe(200);

    // Signing needs an explicit reason and an explicit confirmation.
    const sign = panel.getByRole('button', { name: '确认收到第 1 版', exact: true });
    await expect(sign).toBeDisabled();
    await panel
      .getByLabel('签收说明', { exact: true })
      .fill('客户已收到本月报表、账簿与原件索引，确认签收');
    await panel
      .getByRole('checkbox', { name: '我已核对月份与版本，并确认收到本版全部文件' })
      .check();
    await expect(sign).toBeEnabled();
    if (viewport.width <= 640) {
      const box = await sign.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await sign.click();
    await expect(panel.getByText('客户已签收', { exact: true })).toBeVisible();
    await expect(sign).toHaveCount(0);

    const accepted = await checked(
      await request.get(`/api/books/${f.book.id}/delivery/monthly-package`, {
        headers: f.finance,
      }),
      200,
    );
    const record = accepted.packages[0];
    expect(record.acknowledgement.acknowledged_by).toBe('local-business-finance');
    expect(record.customer_opening_status).toBe('opened');
    expect(record.manifest_sha256).toBe(frozen.manifest_sha256);
    expect(record.external_import_status).toBe('separate_workflow');

    // The firm can never sign for the client, even with the same package id.
    expect(
      (
        await request.post(
          `/api/books/${f.book.id}/delivery/monthly-package/${frozen.id}/acknowledge`,
          {
            headers: f.headers,
            data: { manifest_sha256: frozen.manifest_sha256, reason: '会计再次代签' },
          },
        )
      ).status(),
    ).toBe(403);

    // Signing satisfies the month, so the firm feed stops listing it.
    const settled = await checked(
      await request.get('/api/delivery/followups?limit=50', { headers: f.headers }),
      200,
    );
    expect(
      settled.items.some(
        (item: { book_id: string; period: string }) =>
          item.book_id === f.book.id && item.period === '2026-10',
      ),
    ).toBe(false);

    expect(errors).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: testInfo.outputPath(`q04-client-${viewport.name}.png`),
      fullPage: true,
    });
  });
}
