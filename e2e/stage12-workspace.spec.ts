import { expect, test } from '@playwright/test';
import { choosePersona, openNavigation } from './ui-navigation';
import { presentationRole, workSurface } from '../src/workspace-presentation';

test('四种真实验证身份按主业务角色进入待办首页，兼有编辑权的财务仍先核验业务', async ({
  page,
  request,
}) => {
  // This case includes fixture creation and four real identity changes on the cold dev server.
  test.setTimeout(90_000);
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const ownerHeaders = { Authorization: `Bearer ${owner.token}` },
    financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const company = (
    await (await request.get('/api/business/context', { headers: financeHeaders })).json()
  ).companies[0];
  const book = await (
    await request.post('/api/books', {
      headers: ownerHeaders,
      data: {
        name: `兼有复核权的财务 ${crypto.randomUUID().slice(0, 8)}`,
        company_id: company.id,
        period: '2026-10',
      },
    })
  ).json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: ownerHeaders,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home');
  for (const [persona, role, title] of [
    ['employee', 'employee', '我的工作台'],
    ['department', 'approver', '部门审批'],
    ['finance', 'finance', '财务核验'],
    ['accountant', 'accountant', '事务所管理'],
  ]) {
    const session = await (await request.get(`/api/session?persona=${persona}`)).json();
    const profileResponse = await request.get('/api/workspace/profile', {
      headers: { Authorization: `Bearer ${session.token}` },
    });
    expect(profileResponse.status()).toBe(200);
    const profile = await profileResponse.json();
    expect(profile.role).toBe(role);
    if (persona === 'finance') expect(profile.can_review).toBe(false);
    await choosePersona(page, persona);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await openNavigation(page);
    const client = workSurface(presentationRole(profile)) === 'client';
    const nav = page.getByRole('navigation', { name: client ? '客户服务' : '主导航', exact: true });
    await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
    await expect(page.getByLabel('我的工作概览')).toBeVisible();
    await expect(page.getByLabel('筛选待办类型')).toBeVisible();
    await openNavigation(page);
    if (!client) {
      await expect(page.locator('summary').filter({ hasText: /^设置$/ })).toBeVisible();
      await expect(
        page
          .locator('summary')
          .filter({ hasText: /^设置$/ })
          .locator('..'),
      ).not.toHaveAttribute('open', '');
    } else {
      await expect(page.getByRole('navigation', { name: '设置入口', exact: true })).toHaveCount(0);
      const clientSettings = page.locator('.client-portal-settings');
      if (profile.can_accounting || profile.can_manage) {
        await expect(clientSettings.locator('summary')).toBeVisible();
        await expect(clientSettings).not.toHaveAttribute('open', '');
      } else {
        await expect(clientSettings).toHaveCount(0);
      }
      await expect(page.getByRole('button', { name: '后台运行', exact: true })).toHaveCount(0);
    }
    if (persona === 'employee' || persona === 'department') {
      await expect(page.getByRole('button', { name: '导入流水', exact: true })).toHaveCount(0);
      await expect(
        page
          .getByRole('navigation', { name: '主导航', exact: true })
          .getByRole('button', { name: '跨企业批量复核', exact: true }),
      ).toHaveCount(0);
      if (persona === 'employee') {
        await expect(page.getByRole('region', { name: '提交与进度', exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: /提交报销/ })).toBeVisible();
      } else {
        await expect(page.getByRole('region', { name: '提交与进度', exact: true })).toHaveCount(0);
        await expect(page.getByRole('button', { name: '部门事项', exact: true })).toBeVisible();
      }
    }
    const close = page.getByRole('button', { name: '关闭导航', exact: true });
    if (await close.isVisible()) await close.click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  }
  await page.getByRole('heading', { level: 1 }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'artifacts/stage12-workspace-mobile.png', fullPage: false });
  expect(
    (
      await request.delete(`/api/books/${book.id}/members/local-business-finance`, {
        headers: ownerHeaders,
      })
    ).status(),
  ).toBe(200);
});

test('待办打开前重新核验实时授权，撤权后不展示原件或凭证', async ({ page, request }) => {
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` },
    financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const company = (
    await (await request.get('/api/business/context', { headers: financeHeaders })).json()
  ).companies[0];
  const name = `第十二阶段撤权待办 ${crypto.randomUUID().slice(0, 8)}`;
  const book = await (
    await request.post('/api/books', {
      headers,
      data: { name, company_id: company.id, period: '2026-10' },
    })
  ).json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-reviewer`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '明确合成待办',
          rows: [
            {
              external_id: name,
              date: '2026-10-01',
              description: name,
              counterparty: '虚构供应商',
              amount: '0.30',
              direction: 'expense',
              evidence_ref: '仅合成的待办验证票据',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const draft = (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts[0];
  await page.goto('/workspace/home');
  await page.getByLabel('切换验证身份').selectOption('reviewer');
  await page.getByLabel('筛选待办类型').selectOption('review');
  await expect(page.locator('.workspace-task').filter({ hasText: name })).toBeVisible();
  let revoked = false;
  await page.route(`**/api/workspace/tasks/review/${draft.id}`, async (route) => {
    if (!revoked) {
      revoked = true;
      expect(
        (
          await request.delete(`/api/books/${book.id}/members/local-reviewer`, { headers })
        ).status(),
      ).toBe(200);
    }
    await route.continue();
  });
  try {
    await page
      .locator('.workspace-task')
      .filter({ hasText: name })
      .getByRole('button', { name: '同屏复核', exact: true })
      .click();
    await expect(
      page.getByRole('alert').filter({ hasText: '事项已处理、权限已变更或不存在' }),
    ).toBeVisible();
    await expect(
      page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true }),
    ).toHaveCount(0);
    expect(
      (
        await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers: financeHeaders })
      ).status(),
    ).toBe(404);
  } finally {
    await page.unroute(`**/api/workspace/tasks/review/${draft.id}`);
    expect(
      (
        await request.put(`/api/books/${book.id}/members/local-reviewer`, {
          headers,
          data: { role: 'reviewer' },
        })
      ).status(),
    ).toBe(200);
  }
});

test('审核后导出待办精确打开账套，读取前撤权后不回退到其他企业凭证', async ({ page, request }) => {
  test.setTimeout(60_000);
  const owner = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${owner.token}` },
    financeHeaders = { Authorization: `Bearer ${finance.token}` };
  const company = (
    await (await request.get('/api/business/context', { headers: financeHeaders })).json()
  ).companies[0];
  const suffix = crypto.randomUUID().slice(0, 8);
  const books: { id: string; name: string; description: string; draft_id: string }[] = [];
  for (const [index, label] of ['精确导出目标', '不得回退的其他凭证'].entries()) {
    const name = `${label} ${suffix}`,
      description = `办公用品 · ${label} ${suffix}`;
    let response = await request.post('/api/books', {
      headers,
      data: { name, company_id: company.id, period: '2026-10' },
    });
    expect(response.status()).toBe(201);
    const book = await response.json();
    expect(
      (
        await request.put(`/api/books/${book.id}/members/local-reviewer`, {
          headers,
          data: { role: 'reviewer' },
        })
      ).status(),
    ).toBe(200);
    response = await request.post(`/api/books/${book.id}/imports`, {
      headers,
      data: {
        source_name: '合成待导出来源',
        rows: [
          {
            external_id: `export-${suffix}-${index}`,
            date: '2026-10-01',
            description,
            counterparty: '合成导出验证供方',
            amount: index ? '3.07' : '1.07',
            direction: 'expense',
            evidence_ref: '合成已核验票据，仅验证准确账套导航',
          },
        ],
      },
    });
    expect(response.status()).toBe(200);
    expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(
      200,
    );
    const draft = (await (await request.get(`/api/books/${book.id}`, { headers })).json())
      .drafts[0];
    const reviewed = await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
      headers: financeHeaders,
      data: {
        expected_version: draft.version,
        expected_policy_version: draft.review_responsibility.policy_version,
        review_reason: '本地虚构独立复核后验证导出导航',
      },
    });
    expect(reviewed.status(), await reviewed.text()).toBe(200);
    const responsibility = (await reviewed.json()).review_responsibility;
    expect(responsibility.maker_user_id).toBe('local-accountant');
    expect(responsibility.reviewer_user_id).toBe('local-reviewer');
    expect(responsibility.independent).toBe(true);
    books.push({ id: book.id, name, description, draft_id: draft.id });
  }
  const target = books[0],
    other = books[1];
  const exportWrites: string[] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/exports$/.test(new URL(req.url()).pathname))
      exportWrites.push(req.url());
  });
  await page.goto('/workspace/home');
  await page.getByLabel('筛选待办类型').selectOption('export');
  const task = page.locator(`[data-task-key="export:${target.id}"]`);
  await expect(task).toContainText('待导出凭证 · 1 张');
  await task.getByRole('button', { name: '导出已审核凭证', exact: true }).click();
  await expect(page.getByRole('heading', { name: target.name, exact: true })).toBeVisible();
  await expect(page.getByLabel('凭证摘要', { exact: true })).toHaveValue(target.description);
  await expect(
    page.getByLabel(`选择凭证 ${target.description}`, { exact: true }),
  ).not.toBeChecked();
  await expect(page.getByRole('button', { name: '导出凭证 CSV', exact: true })).toBeDisabled();
  expect(exportWrites).toEqual([]);
  expect(
    (await (await request.get(`/api/books/${target.id}/exports`, { headers })).json()).exports,
  ).toEqual([]);

  await page.getByLabel('切换验证身份').selectOption('reviewer');
  await expect(page.getByRole('heading', { name: '会计复核', exact: true })).toBeVisible();
  await page.getByLabel('筛选待办类型').selectOption('export');
  await expect(task).toBeVisible();
  let revoked = false;
  const targetRead = (url: URL) =>
    url.pathname === `/api/books/${target.id}` && url.searchParams.get('summaries') === 'true';
  await page.route(targetRead, async (route) => {
    if (!revoked) {
      revoked = true;
      expect(
        (
          await request.delete(`/api/books/${target.id}/members/local-reviewer`, {
            headers,
          })
        ).status(),
      ).toBe(200);
    }
    await route.continue();
  });
  try {
    await task.getByRole('button', { name: '导出已审核凭证', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: /不存在或.*无权访问/ })).toBeVisible();
    expect(revoked).toBe(true);
    await expect(page.getByLabel('凭证摘要', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '确认审核', exact: true })).toHaveCount(0);
    await expect(page.getByLabel(`选择凭证 ${other.description}`, { exact: true })).toHaveCount(0);
    expect(
      (
        await request.get(`/api/books/${target.id}/drafts/${target.draft_id}`, {
          headers: financeHeaders,
        })
      ).status(),
    ).toBe(404);
    expect(
      (
        await request.get(`/api/books/${other.id}/drafts/${other.draft_id}`, {
          headers: financeHeaders,
        })
      ).status(),
    ).toBe(200);
    expect(exportWrites).toEqual([]);
    expect(
      (await (await request.get(`/api/books/${target.id}/exports`, { headers })).json()).exports,
    ).toEqual([]);
  } finally {
    await page.unroute(targetRead);
    for (const book of books)
      expect(
        (
          await request.delete(`/api/books/${book.id}/members/local-reviewer`, { headers })
        ).status(),
      ).toBe(200);
  }
});
