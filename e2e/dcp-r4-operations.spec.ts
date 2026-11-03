import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('DCP R4 从开期进入逐户管理后旧轮询停止', async ({ page }) => {
  const batch = {
    id: 'poll-batch',
    kind: 'monthly_open',
    request: { period: '2026-11' },
    status: 'running',
    total: 1,
    succeeded: 0,
    skipped: 0,
    failed: 0,
    cancelled: 0,
    progress: { total: 1, completed: 0, percent: 0 },
    items: [
      { id: 'i', company_id: 'co', company_name: '轮询合成企业', status: 'running', result: null },
    ],
  };
  let reads = 0;
  await page.route('**/api/firm/batch-operations/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/preview'))
      return route.fulfill({
        json: {
          period: '2026-11',
          ready: 1,
          blocked: 0,
          skipped: 0,
          notes: [],
          results: [{ company_id: 'co', company_name: '轮询合成企业', status: 'ready' }],
        },
      });
    if (path.endsWith('/poll-batch')) reads++;
    return route.fulfill({ json: batch });
  });
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.goto('/workspace/monthly');
  await page.getByRole('button', { name: '批量开期', exact: true }).click();
  const open = page.getByRole('dialog', { name: '月初批量开期', exact: true });
  await open.getByLabel('批量开期操作依据').fill('测试逐户管理与轮询清理');
  await open.getByRole('button', { name: '预检范围' }).click();
  await open.getByRole('button', { name: '开始批量开期' }).click();
  await expect(open.getByRole('button', { name: '管理逐户任务' })).toBeVisible();
  await Promise.all([
    page.waitForResponse('**/api/firm/batch-operations/poll-batch'),
    open.getByRole('button', { name: '管理逐户任务' }).click(),
  ]);
  await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toContainText(
    '轮询合成企业',
  );
  const before = reads;
  await page.waitForTimeout(1200); // old 800ms timer must be gone; new panel polls at 2s
  expect(reads).toBe(before);
});

for (const width of [1280, 390]) {
  test(`DCP R4 批量办理与接手 ${width}`, async ({ page, request }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 844 });
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const tag = crypto.randomUUID().slice(0, 6);
    const company = await (
      await request.post('/api/companies', {
        headers,
        data: { name: `R4合成企业${tag}`, tax_id: '' },
      })
    ).json();
    await request.post('/api/books', {
      headers,
      data: { name: `R4账套${tag}`, company_id: company.id, period: '2026-11' },
    });
    const calls: { path: string; body: Record<string, unknown> }[] = [];
    let conflict = true;
    const batch = {
      id: 'r4-batch',
      kind: 'monthly_open',
      request: { period: '2026-11' },
      status: 'running',
      progress: { total: 4, completed: 1, percent: 25 },
      items: [
        {
          id: 'run',
          company_id: 'run-co',
          company_name: '运行中企业',
          status: 'running',
          cancel_requested_at: null as string | null,
        },
        {
          id: 'queue',
          company_id: 'queue-co',
          company_name: '排队企业',
          status: 'queued',
          cancel_requested_at: null,
        },
        {
          id: 'skip',
          company_id: 'skip-co',
          company_name: '跳过企业',
          status: 'queued',
          cancel_requested_at: null,
        },
        {
          id: 'unknown',
          company_id: 'unknown-co',
          company_name: '未知结果企业',
          status: 'failed',
          error_code: 'response_unknown',
          attempt: 1,
          max_attempts: 3,
        },
      ],
    };
    await page.route('**/api/firm/batch-operations**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      const body = route.request().method() === 'POST' ? route.request().postDataJSON() : {};
      if (route.request().method() === 'POST') calls.push({ path, body });
      if (path.endsWith('/batch-operations'))
        return route.fulfill({ json: { batches: [batch], total: 1 } });
      if (path.endsWith('/r4-batch')) return route.fulfill({ json: batch });
      if (/\/items\//.test(path)) {
        expect(body.reason.length).toBeGreaterThanOrEqual(3);
        const item = batch.items.find((i) => path.includes(`/items/${i.id}/`))!;
        if (item.status === 'running') item.cancel_requested_at = '2026-11-02';
        else item.status = path.endsWith('/skip') ? 'skipped' : 'cancelled';
        return route.fulfill({ json: item });
      }
      const isPackage = path.includes('package-draft');
      if (path.endsWith('/preview'))
        return route.fulfill({
          json: {
            period: '2026-11',
            ready: 1,
            blocked: 0,
            manifest: 'a'.repeat(64),
            companies: [
              {
                company_id: company.id,
                book_id: 'book',
                book_name: '本月账套',
                ...(isPackage
                  ? { report_id: 'report', report_version: 3, report_status: 'published' }
                  : { drafts: [{ draft_id: 'draft', version: 2 }], manifest: 'b'.repeat(64) }),
              },
            ],
          },
        });
      if (conflict) {
        conflict = false;
        return route.fulfill({ status: 409, json: { detail: 'version changed' } });
      }
      expect(body.confirmed_manifest).toBe('a'.repeat(64));
      expect(body.company_ids).toEqual([company.id]);
      return route.fulfill({
        json: {
          id: 'created',
          kind: isPackage ? 'package_draft' : 'review_export',
          request: { period: '2026-11' },
          status: 'completed',
          progress: { total: 1, completed: 1, percent: 100 },
          items: [
            {
              id: 'done',
              company_id: company.id,
              company_name: company.name,
              status: 'succeeded',
              result: {
                book_id: 'book',
                package_id: isPackage ? 'a'.repeat(32) : undefined,
                export_id: isPackage ? undefined : 'export',
              },
            },
          ],
        },
      });
    });
    let version = 1,
      assigned = false,
      assignCount = 0;
    const handoff = () => ({
      source: { kind: 'tax_workpaper', id: 'paper', version, fingerprint: 'c'.repeat(64) },
      scope: {
        book_id: 'book',
        period: '2026-11',
        company_name: '待接手企业',
        book_name: '本月账套',
      },
      role: 'reviewer',
      maker_id: 'maker',
      next_action: '复核税务底稿并出具意见',
      assignee_id: assigned ? 'reviewer' : null,
      status: assigned ? 'assigned' : 'pending',
      due_on: assigned ? '2026-11-15' : null,
    });
    await page.route('**/api/handoff**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/candidates'))
        return route.fulfill({
          json: {
            items: [
              { user_id: 'maker', name: '制单人' },
              { user_id: 'reviewer', name: '独立复核人' },
            ],
          },
        });
      if (path.endsWith('/assign')) {
        assignCount++;
        const body = route.request().postDataJSON();
        expect(body.expected_version).toBe(version);
        expect(body.assignee_id).toBe('reviewer');
        expect(body.due_on).toBe('2026-11-15');
        expect(body.expected_fingerprint).toBe('c'.repeat(64));
        if (version === 1) {
          version++;
          return route.fulfill({ status: 409, json: { detail: 'stale' } });
        }
        assigned = true;
        return route.fulfill({ json: handoff() });
      }
      return route.fulfill({ json: { items: [handoff()], total: 1 } });
    });
    await page.goto('/');
    await choosePersona(page, 'accountant');
    await page.goto('/workspace/monthly');
    await page.getByLabel('客户进度月份').fill('2026-11');
    await page.getByLabel('搜索月度客户').fill(`R4合成企业${tag}`);
    await page.getByRole('button', { name: '查找', exact: true }).click();
    await expect(page.getByText(`R4合成企业${tag}`).first()).toBeVisible();
    await page.getByRole('button', { name: '批量办理', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toBeVisible();
    const dialog = page.getByRole('dialog', { name: '批量办理', exact: true });
    await dialog.getByRole('button', { name: /2026-11 · 月初开期/ }).click();
    const running = dialog.locator('article').filter({ hasText: '运行中企业' });
    await running.getByRole('button', { name: '取消此项' }).click();
    await expect(dialog.getByRole('button', { name: '确认取消申请' })).toBeDisabled();
    await dialog.getByLabel('逐户操作原因').fill('人工确认停止该项');
    await dialog.getByRole('button', { name: '确认取消申请' }).click();
    await expect(running).toContainText('取消已申请');
    await expect(running.getByRole('button', { name: '取消此项' })).toHaveCount(0);
    for (const [name, verb] of [
      ['排队企业', '取消'],
      ['跳过企业', '跳过'],
    ]) {
      await dialog
        .locator('article')
        .filter({ hasText: name })
        .getByRole('button', { name: `${verb}此项` })
        .click();
      await dialog.getByLabel('逐户操作原因').fill('人工确认保留操作依据');
      await dialog
        .getByRole('button', { name: verb === '取消' ? '确认取消申请' : '确认跳过' })
        .click();
      await expect(dialog.locator('article').filter({ hasText: name })).toContainText(`已${verb}`);
    }
    await expect(dialog.getByRole('button', { name: '只重试失败项' })).toBeDisabled();
    await dialog.screenshot({ path: `artifacts/dcp-r4-batch-${width}.png` });
    await dialog.getByRole('button', { name: '返回批量选择' }).click();
    await dialog.getByLabel(company.name).check();
    await dialog.getByRole('button', { name: '预检所选企业' }).click();
    await dialog.getByRole('button', { name: '确认办理 1 家' }).click();
    await expect(dialog.getByRole('alert')).toContainText('数据已被其他操作更新');
    await expect(dialog.getByRole('button', { name: '确认办理 0 家' })).toBeDisabled();
    expect(calls.filter((c) => c.path.endsWith('/review-export'))).toHaveLength(1);
    await dialog.getByRole('button', { name: '预检所选企业' }).click();
    await dialog.getByRole('button', { name: '确认办理 1 家' }).click();
    await expect(dialog).toContainText('已生成冻结导出记录');
    await page.route('**/api/books/book/exports/export', (route) =>
      route.fulfill({
        json: { id: 'export', csv: '凭证号,金额\n1,100\n', filename: 'r4-vouchers.csv' },
      }),
    );
    await expect(dialog.getByRole('button', { name: '下载凭证 CSV', exact: true })).toBeVisible();
    const download = page.waitForEvent('download');
    await dialog.getByRole('button', { name: '下载凭证 CSV', exact: true }).click();
    expect((await download).suggestedFilename()).toBe('r4-vouchers.csv');
    let unexpectedDownloads = 0;
    page.on('download', () => unexpectedDownloads++);
    await page.route('**/api/books/book/exports/export', (route) =>
      route.fulfill({ json: { id: 'foreign-export', csv: '不应下载', filename: 'wrong.csv' } }),
    );
    await dialog.getByRole('button', { name: '下载凭证 CSV', exact: true }).click();
    await expect(dialog.getByRole('alert')).toBeVisible();
    expect(unexpectedDownloads).toBe(0);
    await dialog.getByRole('button', { name: '返回批量选择' }).click();
    await dialog.getByLabel('批量办理内容').selectOption('package_draft');
    await dialog.getByRole('button', { name: '预检所选企业' }).click();
    await dialog.getByRole('button', { name: '确认办理 1 家' }).click();
    await expect(dialog).toContainText('交付包草稿已生成');
    await dialog.screenshot({ path: `artifacts/dcp-r4-package-${width}.png` });
    await dialog.getByRole('button', { name: '关闭' }).click();
    await page.getByRole('button', { name: '人工接手', exact: true }).click();
    const handoffDialog = page.getByRole('dialog', { name: '人工接手', exact: true });
    for (let attempt = 0; attempt < 2; attempt++) {
      await handoffDialog.getByRole('button', { name: '选择负责人' }).click();
      await expect(handoffDialog.getByLabel('接手人')).toHaveValue('');
      await expect(
        handoffDialog.getByLabel('接手人').getByRole('option', { name: '制单人', exact: true }),
      ).toHaveCount(0);
      await handoffDialog.getByLabel('接手人').selectOption('reviewer');
      await handoffDialog.getByLabel('办理期限').fill('2026-11-15');
      await handoffDialog.getByLabel('指派依据').fill('独立复核排期安排');
      await handoffDialog.getByRole('button', { name: '确认指派' }).click();
      if (!attempt) {
        await expect(handoffDialog.getByRole('alert')).toContainText('数据已被其他操作更新');
        expect(assignCount).toBe(1);
        await handoffDialog.getByRole('button', { name: '刷新接手队列' }).click();
        await expect(handoffDialog).toContainText('底稿版本 2');
      }
    }
    await expect(handoffDialog).toContainText('已指派');
    await handoffDialog.screenshot({ path: `artifacts/dcp-r4-handoff-${width}.png` });
    expect(calls.filter((c) => c.path.endsWith('/cancel'))).toHaveLength(2);
    expect(calls.some((c) => /\/(publish|send|review)$/.test(c.path))).toBe(false);
    expect(assignCount).toBe(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await handoffDialog.getByRole('button', { name: '关闭弹窗' }).click();
    await page.getByRole('button', { name: '批量办理', exact: true }).click();
    await page.route('**/api/firm/batch-operations/r4-batch', (route) =>
      route.fulfill({ json: { ...batch, id: 'another-tenant-batch' } }),
    );
    await dialog.getByRole('button', { name: /2026-11 · 月初开期/ }).click();
    await expect(dialog.getByRole('alert')).toBeVisible();
    await expect(dialog.getByRole('region', { name: '批量逐户结果' })).toHaveCount(0);
    await expect(dialog.getByText('运行中企业', { exact: true })).toHaveCount(0);
    await page.route('**/api/firm/batch-operations/r4-batch', (route) =>
      route.fulfill({ status: 403, json: { detail: 'revoked' } }),
    );
    await dialog.getByRole('button', { name: /2026-11 · 月初开期/ }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/workspace\/monthly/);
  });
}

test('DCP R4 实际服务预检阻断与岗位入口', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const name = `R4真实契约${crypto.randomUUID().slice(0, 6)}`;
  const company = await (
    await request.post('/api/companies', { headers, data: { name, tax_id: '' } })
  ).json();
  await request.post('/api/books', {
    headers,
    data: { name, company_id: company.id, period: '2026-11' },
  });
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.goto('/workspace/monthly');
  await page.getByLabel('客户进度月份').fill('2026-11');
  await page.getByLabel('搜索月度客户').fill(name);
  await page.getByRole('button', { name: '查找', exact: true }).click();
  await expect(page.getByText(name).first()).toBeVisible();
  await page.getByRole('button', { name: '批量办理', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '批量办理', exact: true });
  await dialog.getByLabel(name).check();
  await dialog.getByRole('button', { name: '预检所选企业' }).click();
  await expect(dialog).toContainText('没有已复核的待导出凭证');
  await expect(dialog.getByRole('button', { name: '确认办理 0 家' })).toBeDisabled();
  await dialog.getByLabel('批量办理内容').selectOption('package_draft');
  await dialog.getByRole('button', { name: '预检所选企业' }).click();
  await expect(dialog).toContainText('缺少已复核发布的报表');
  await expect(dialog.getByRole('button', { name: '确认办理 0 家' })).toBeDisabled();
  await dialog.getByRole('button', { name: '关闭弹窗' }).click();
  await choosePersona(page, 'reviewer');
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  const monthly = nav.getByRole('button', { name: '月度交付', exact: true });
  if (!(await monthly.isVisible()))
    await nav
      .locator('summary')
      .filter({ hasText: /^日常业务/ })
      .click();
  await monthly.click();
  await expect(page.getByRole('button', { name: '刷新进度' })).toBeVisible();
  await expect(page.getByRole('button', { name: '批量办理', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '人工接手', exact: true }).click();
  await expect(
    page.getByRole('dialog', { name: '人工接手' }).getByRole('button', { name: '选择负责人' }),
  ).toHaveCount(0);
});
