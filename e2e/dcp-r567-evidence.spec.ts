import { test, expect } from '@playwright/test';

for (const width of [1280, 390]) {
  test(`DCP 核查与只读调查 ${width}`, async ({ page, request }, info) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 844 });
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const me = await (await request.get('/api/me', { headers })).json();
    expect(me.current_firm_id).toBeTruthy();
    const company = await (
      await request.post('/api/companies', {
        headers,
        data: { name: 'DCP合成核查' + crypto.randomUUID().slice(0, 6), tax_id: '' },
      })
    ).json();
    const book = await (
      await request.post('/api/books', {
        headers,
        data: { name: 'DCP核查账套', company_id: company.id, period: '2026-10' },
      })
    ).json();
    const item = {
      id: 'synthetic-unknown',
      kind: 'signature_reminder',
      state: 'unknown',
      semantic: 'unknown',
      attempts: 1,
      recipient: { user_id: 'recipient', name: '虚构联系人' },
      package: { id: 'package-one', version: 2, manifest_sha256: 'a'.repeat(64) },
      investigation: null as Record<string, unknown> | null,
      can_investigate: true,
      channel: 'local_sink',
      last_error: '',
      created_at: '2026-11-02T00:00:00Z',
      updated_at: '',
      dispatched_at: '',
      opened_at: '',
      acknowledged_at: '',
    };
    const posts: Record<string, unknown>[] = [];
    let deny = false;
    await page.route('**/api/books/*/delivery/notifications**', async (route) => {
      if (deny) return route.fulfill({ status: 403, json: { detail: '权限已撤销' } });
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON();
        posts.push(body);
        item.investigation = {
          id: 'manual-proof',
          revision: 1,
          conclusion: body.conclusion,
          reason: body.reason,
          evidence_ref: body.evidence_ref,
          created_at: '2026-11-02T01:00:00Z',
        };
        return route.fulfill({ status: 201, json: item.investigation });
      }
      return route.fulfill({ json: { book_id: book.id, period: book.period, items: [item] } });
    });
    await page.route('**/api/assistant/investigations/capability?*', (route) =>
      route.fulfill({ json: { enabled: true, book_id: book.id } }),
    );
    const result = {
      book_id: book.id,
      period: book.period,
      status: 'complete',
      model_calls: 0,
      coverage: { read: 3, of: 3 },
      missing: [],
      steps: ['voucher_status', 'close_blockers', 'delivery_status'].map((tool) => ({
        tool,
        ok: true,
      })),
      evidence: {
        firm_id: me.current_firm_id,
        company_id: company.id,
        fingerprint: 'b'.repeat(64),
      },
      findings: {
        voucher_status: { posted_vouchers: 0, draft_rows: 1 },
        close_blockers: { status: 'no_run', blockers: [], version: null },
        delivery_status: { package: { version: 2 }, opened: false, notifications: { unknown: 1 } },
      },
    };
    let badScope = false;
    await page.route('**/api/assistant/investigations', (route) =>
      route.fulfill({ json: badScope ? { ...result, book_id: 'foreign-book' } : result }),
    );
    await page.goto(
      `/workspace/delivery?book=${book.id}&company=${company.id}&period=${book.period}`,
    );
    const notifications = page.getByRole('region', { name: '提醒发送状态' });
    await expect(notifications.getByText('结果未知', { exact: true })).toBeVisible();
    await notifications.getByRole('button', { name: '登记人工核查' }).click();
    await notifications.getByLabel('核查证据编号或说明').fill('SYNTHETIC-001 本地核查');
    await notifications.getByLabel('核查原因').fill('通道结果仍需人工核对');
    await notifications.getByRole('button', { name: '保存核查记录' }).click();
    await expect(notifications).toContainText('第 1 次核查');
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatchObject({
      expected_revision: 0,
      expected_attempts: 1,
      expected_package_version: 2,
      expected_recipient_user_id: 'recipient',
      expected_manifest_sha256: 'a'.repeat(64),
      conclusion: 'unresolved',
    });
    await expect(notifications.getByText('结果未知', { exact: true })).toBeVisible();
    await expect(notifications.getByText('客户已签收', { exact: true })).toHaveCount(0);
    const panel = page.getByRole('region', { name: '交付依据调查' });
    await panel.getByRole('button', { name: '核对交付依据' }).click();
    await expect(panel).toContainText('已核对 3 / 3');
    await panel.getByText('交付与通知情况', { exact: true }).click();
    await expect(panel).toContainText('发送结果未知，需管理员人工核查');
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`evidence-${width}.png`), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    badScope = true;
    await panel.getByRole('button', { name: '核对交付依据' }).click();
    await expect(panel.getByRole('alert')).toBeVisible();
    await expect(panel).not.toContainText('已核对 3 / 3');
    deny = true;
    await notifications.getByRole('button', { name: '刷新', exact: true }).click();
    // Global revocation may unmount the entire protected workbench.
    await expect(page.locator('body')).not.toContainText('虚构联系人');
    await expect(page.locator('body')).not.toContainText('第 1 次核查');
    expect(posts).toHaveLength(1);
  });
}
