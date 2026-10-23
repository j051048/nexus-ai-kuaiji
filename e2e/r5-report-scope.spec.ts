import { expect, test, type Page } from '@playwright/test';

const companies = [
  { id: 'r5-company-a', name: '合成企业A', task_count: 0 },
  { id: 'r5-company-b', name: '合成企业B', task_count: 0 },
];
const reports = companies.map((company, i) => ({
  company_id: company.id,
  company_name: company.name,
  book_id: `r5-book-${i ? 'b' : 'a'}`,
  book_name: `合成账套${i ? 'B' : 'A'}`,
  period: '2026-10',
}));
type Report = (typeof reports)[number];

async function portal(
  page: Page,
  options: {
    role?: string;
    companies?: typeof companies;
    reports?: Report[];
    delayA?: Promise<void>;
    denyB?: boolean;
  } = {},
) {
  const available = options.reports ?? reports;
  const firms = options.companies ?? companies;
  const requests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const tasks = {
    tasks: [],
    counts: { action: 0, waiting: 0 },
    pagination: { total: 0, limit: 20, offset: 0, has_more: false },
  };
  const permissions = { edit: false, review: false, export: false, manage: false };
  const stats = {
    book_count: available.length,
    pending_count: 0,
    exception_count: 0,
    approved_count: 0,
    source_count: 0,
  };
  const profile = {
    role: options.role ?? 'manager',
    display_role: options.role ?? 'manager',
    can_submit: false,
    can_approve: false,
    can_finance: false,
    can_review: false,
    can_accounting: false,
    can_manage: options.role !== 'viewer',
    companies: firms,
    briefings: available,
    book_count: available.length,
  };
  const me = {
    mode: 'local',
    user: { id: 'r5-client', name: '合成客户', email: 'r5@example.invalid' },
    firms: [{ id: 'r5-firm', name: '合成事务所', role: 'client_admin' }],
    current_firm_id: 'r5-firm',
    permissions: {
      manage_firm: false,
      manage_admin: false,
      create_company: false,
      create_book: false,
    },
  };
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    requests.push(url.pathname + url.search);
    let data: unknown = {};
    if (url.pathname === '/api/auth/config')
      data = {
        mode: 'local',
        allow_firm_signup: false,
        personas: [{ id: 'accountant', label: '合成客户' }],
      };
    else if (url.pathname === '/api/session') data = { token: 'synthetic-r5-token' };
    else if (url.pathname === '/api/me') data = me;
    else if (url.pathname === '/api/workspace/bootstrap') data = { me, profile, tasks };
    else if (url.pathname === '/api/workspace/profile') data = profile;
    else if (url.pathname === '/api/workspace/companies')
      data = {
        companies: firms,
        pagination: { total: firms.length, limit: 20, offset: 0, has_more: false },
      };
    else if (url.pathname === '/api/workspace/tasks') data = tasks;
    else if (url.pathname === '/api/agent-workspace/summary')
      data = {
        counts: { active: 0 },
        runs: [],
        pending: [],
        stages: [],
        companies: [],
      };
    else if (url.pathname === '/api/workbench')
      data = {
        books: available.map((r) => ({
          ...r,
          id: r.book_id,
          name: r.book_name,
          permissions,
          pending_count: 0,
          exception_count: 0,
          approved_count: 0,
        })),
        stats,
        pagination: { total: available.length, limit: 20, offset: 0, has_more: false },
      };
    else if (/^\/api\/books\//.test(url.pathname)) {
      const report = available.find((r) => r.book_id === url.pathname.split('/')[3]);
      if (!report) return route.fulfill({ status: 404, json: { detail: '账套不可访问' } });
      data = {
        book: { ...report, id: report.book_id, name: report.book_name, permissions },
        accounts: [],
        drafts: [],
        sources: [],
        stats,
        exports: [],
        pagination: { total: 0, limit: 20, offset: 0, has_more: false },
      };
    } else if (/\/briefing$/.test(url.pathname)) {
      const report = available.find((r) => url.pathname.includes(`/books/${r.book_id}/`));
      if (!report || (options.denyB && report.company_id === companies[1].id))
        return route.fulfill({ status: 403, json: { detail: '当前企业报告权限已撤销' } });
      if (report.company_id === companies[0].id && options.delayA) await options.delayA;
      data = {
        book: { ...report, id: report.book_id, name: report.book_name, period_status: 'closed' },
        flows: { income: '0', expense: '0', net: '0', basis: '合成' },
        top_expenses: [],
        balances: { receivable: null, payable: null, basis: '合成' },
        tax: { change: '0', note: '合成' },
        risks: [],
        notes: [],
        generated_at: '2026-10-20',
      };
    }
    return route.fulfill({ status: 200, json: data });
  });
  await page.goto('/workspace/home');
  await expect(page.getByRole('navigation', { name: '客户服务' })).toBeVisible();
  return { requests, errors };
}

async function pick(page: Page, company: string) {
  const response = page.waitForResponse((r) => {
    const url = new URL(r.url());
    return (
      url.pathname === '/api/workspace/tasks' &&
      url.searchParams.get('company_id') === companies.find((c) => c.name === company)?.id
    );
  });
  await page.getByRole('button', { name: '筛选待办企业', exact: true }).click();
  await page
    .getByRole('region', { name: '企业快搜' })
    .getByRole('button', { name: new RegExp(company) })
    .click();
  await response;
}
async function briefing(page: Page) {
  await page
    .getByRole('navigation', { name: '客户服务' })
    .getByRole('button', { name: '报告', exact: true })
    .click();
}
async function assertB(page: Page, requests: string[]) {
  await expect(
    page.getByRole('heading', { name: '合成企业B · 2026-10 财税简报', exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/book=r5-book-b/);
  await expect(page).toHaveURL(/company=r5-company-b/);
  expect(requests.filter((url) => url.endsWith('/briefing')).at(-1)).toBe(
    '/api/monthly/books/r5-book-b/briefing',
  );
}

test('U01 保存B视图、切A、恢复B后，任务、地址与报告都使用B', async ({ page }) => {
  const { requests, errors } = await portal(page);
  await page.getByRole('button', { name: '显示列表选项', exact: true }).click();
  await page.getByRole('button', { name: '紧凑表格视图', exact: true }).click();
  await pick(page, '合成企业B');
  await expect(page.getByRole('button', { name: '紧凑表格视图', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.getByRole('button', { name: '保存视图', exact: true }).click();
  await pick(page, '合成企业A');
  const restored = page.waitForResponse(
    (r) => r.url().includes('/workspace/tasks?') && r.url().includes('company_id=r5-company-b'),
  );
  await page.getByRole('button', { name: '1 · 待处理 · 指定企业', exact: true }).click();
  await restored;
  await expect(page.getByRole('button', { name: '紧凑表格视图', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page).toHaveURL(/company=r5-company-b/);
  await briefing(page);
  await assertB(page, requests);
  expect(requests.filter((url) => url.endsWith('/briefing'))).not.toContain(
    '/api/monthly/books/r5-book-a/briefing',
  );
  expect(errors).toEqual([]);
});

test('U02 看A报告、返回待办、选B后不会沿用A账套', async ({ page }) => {
  const { requests } = await portal(page);
  await pick(page, '合成企业A');
  await briefing(page);
  await expect(page.getByRole('heading', { name: '合成企业A · 2026-10 财税简报' })).toBeVisible();
  await page
    .getByRole('navigation', { name: '客户服务' })
    .getByRole('button', { name: '企业首页', exact: true })
    .click();
  await pick(page, '合成企业B');
  await briefing(page);
  await assertB(page, requests);
});

test('A迟到报告不能覆盖B报告', async ({ page }) => {
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const { requests } = await portal(page, { delayA: delayed });
  try {
    await pick(page, '合成企业A');
    const pending = page.waitForRequest((r) => r.url().endsWith('/books/r5-book-a/briefing'));
    await briefing(page);
    await pending;
    await page
      .getByRole('navigation', { name: '客户服务' })
      .getByRole('button', { name: '企业首页', exact: true })
      .click();
    await pick(page, '合成企业B');
    await briefing(page);
    await assertB(page, requests);
  } finally {
    release();
  }
  await expect(page.getByRole('heading', { name: '合成企业A · 2026-10 财税简报' })).toHaveCount(0);
});

test('B撤权显示明确错误，不展示A或跳回登录', async ({ page }) => {
  await portal(page, { denyB: true });
  await pick(page, '合成企业B');
  await briefing(page);
  await expect(page.getByText('当前企业报告权限已撤销', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/company=r5-company-b/);
  await expect(page.locator('.briefing-card')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: '客户服务' })).toBeVisible();
});

for (const width of [1280, 375])
  test(`多报告必须选择；${width}px只读客户按企业账套期间打开报告`, async ({ page }) => {
    await page.setViewportSize({ width, height: 812 });
    const available = [reports[1], { ...reports[1], book_id: 'r5-book-b-sep', period: '2026-09' }];
    const { requests } = await portal(page, {
      role: 'viewer',
      companies: [companies[1]],
      reports: available,
    });
    await briefing(page);
    const chooser = page.getByRole('dialog', { name: '选择财税简报' });
    await expect(chooser).toBeVisible();
    expect(requests.filter((url) => url.endsWith('/briefing'))).toEqual([]);
    await chooser.getByRole('button', { name: /合成企业B.*2026-09/ }).click();
    await expect(page.getByRole('heading', { name: '合成企业B · 2026-09 财税简报' })).toBeVisible();
    await expect(page).toHaveURL(/book=r5-book-b-sep/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });

test('选中企业无报告时保留范围，不偷偷回退到其他企业', async ({ page }) => {
  const { requests } = await portal(page, { reports: [reports[0]] });
  await pick(page, '合成企业B');
  await briefing(page);
  await expect(page.getByRole('dialog', { name: '选择财税简报' })).toBeVisible();
  await expect(page.getByText('当前企业范围没有可打开的报告。', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/company=r5-company-b/);
  expect(requests.filter((url) => url.endsWith('/briefing'))).toEqual([]);
});
