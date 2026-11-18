import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

const kinds = [
  'intake',
  'business',
  'approval',
  'finance',
  'handoff',
  'case',
  'material',
  'review',
  'ocr',
  'proposal',
  'export',
  'delivery',
  'alert',
  'migration',
] as const;

test('PLT03-C 十四类待办均离开首页并带目标范围', async ({ page, request }) => {
  test.setTimeout(180_000);
  const session = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const context = await (await request.get('/api/business/context', { headers })).json();
  const company = context.companies[0];
  const book = context.books?.[0];
  const stamp = 'plt03-matrix';
  const task = (kind: (typeof kinds)[number]) => ({
    key: `${kind}:${stamp}-${kind}`,
    kind,
    ref: `${stamp}-${kind}`,
    company_id: company.id,
    company_name: company.name,
    book_id: book?.id ?? `${stamp}-book`,
    event_id:
      kind === 'intake' ||
      kind === 'business' ||
      kind === 'approval' ||
      kind === 'finance' ||
      kind === 'handoff'
        ? `${stamp}-event`
        : null,
    draft_id: kind === 'review' ? `${stamp}-draft` : null,
    batch_id: kind === 'ocr' ? `${stamp}-batch` : null,
    title: `${kind} 合成待办`,
    state: 'pending',
    priority: 1,
    due_on: '2026-10-20',
    updated_at: '2026-10-18T10:00:00+00:00',
    bucket: 'action',
    action_label: '办理',
    responsible: '合成处理人',
    overdue: false,
    period: '2026-10',
    object_type: kind,
    object_id: kind === 'review' ? `${stamp}-draft` : `${stamp}-${kind}`,
    object_version: '2026-10-18T10:00:00+00:00',
    available_action: 'can_act',
    business_action: '办理',
    current_handler: '合成处理人',
    alternate_actions: [],
    amount_impact: null,
  });
  const tasks = kinds.map(task);

  await page.route('**/api/workspace/bootstrap**', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: {
        ...body,
        tasks: {
          tasks,
          counts: { action: tasks.length },
          pagination: { total: tasks.length, limit: 20, offset: 0, has_more: false },
        },
      },
    });
  });
  await page.route('**/api/workspace/tasks**', async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts.length >= 5) {
      const kind = parts[3] as (typeof kinds)[number];
      const item = tasks.find((entry) => entry.kind === kind && entry.ref === parts[4]);
      await route.fulfill({ json: item });
      return;
    }
    await route.fulfill({
      json: {
        tasks,
        counts: { action: tasks.length },
        pagination: { total: tasks.length, limit: 20, offset: 0, has_more: false },
      },
    });
  });

  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  await expect(page.locator('.workspace-task')).toHaveCount(14, { timeout: 15_000 });

  const observations: Array<{ kind: string; url: string; text: string }> = [];
  for (const kind of kinds) {
    const row = page.locator(`[data-task-key="${kind}:${stamp}-${kind}"]`);
    await row.getByRole('button').last().click();
    await page.waitForTimeout(250);
    observations.push({
      kind,
      url: page.url(),
      text: (await page.locator('body').innerText()).slice(0, 180),
    });
    await page.goto('/workspace/home');
    await expect(page.locator('.workspace-task')).toHaveCount(14, { timeout: 15_000 });
  }
  const expected: Record<(typeof kinds)[number], string> = {
    intake: `intake=${stamp}-intake`,
    business: `event=${stamp}-event`,
    approval: `event=${stamp}-event`,
    finance: `event=${stamp}-event`,
    handoff: `event=${stamp}-event`,
    case: `case=${stamp}-case`,
    material: `material=${stamp}-material`,
    review: `review_draft=${stamp}-draft`,
    ocr: `job=${stamp}-ocr`,
    proposal: `proposal=${stamp}-proposal`,
    export: `filter=approved`,
    delivery: `delivery=${stamp}-delivery`,
    alert: `alert=${stamp}-alert`,
    migration: `migration=${stamp}-migration`,
  };
  for (const observation of observations) {
    expect(observation.url, observation.kind).toContain(expected[observation.kind]);
    expect(observation.url, `${observation.kind} company`).toContain(`company=${company.id}`);
    expect(observation.url, `${observation.kind} period`).toContain('period=2026-10');
    expect(observation.url, `${observation.kind} version`).toContain(
      'task_version=2026-10-18T10%3A00%3A00%2B00%3A00',
    );
    if (observation.kind === 'alert') {
      expect(observation.url, 'alert page').toContain('/workspace/operations');
    }
  }
  expect(observations).toHaveLength(14);
});
