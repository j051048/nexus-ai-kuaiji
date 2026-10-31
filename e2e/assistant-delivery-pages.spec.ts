import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
let sharedBook: { id: string; company_id: string };
test.beforeAll(async ({ request }) => {
  // These cases only read mocked detail projections; reuse one real book and
  // isolate conversations. Creating fourteen extra firms would push unrelated
  // existing list tests beyond their first page in the shared local server.
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const created = await request.post('/api/books', {
    headers: { Authorization: `Bearer ${session.token}` },
    data: { name: `分页-${crypto.randomUUID()}`, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  sharedBook = await created.json();
});
for (const width of [1440, 390]) {
  for (const fault of [
    'none',
    'stale',
    'revoked',
    'wrong-scope',
    'late',
    'mfa',
    'server',
  ] as const) {
    test(`同版本明细分页 ${width} ${fault}`, async ({ page, request }) => {
      await page.setViewportSize({ width, height: 900 });
      const session = await (await request.get('/api/session?persona=accountant')).json();
      const headers = { Authorization: `Bearer ${session.token}` };
      const book = sharedBook;
      const conversation = (
        await (
          await request.post('/api/assistant/conversations', {
            headers,
            data: {
              company_id: book.company_id,
              book_ids: [book.id],
              title: `分页诊断-${crypto.randomUUID()}`,
              idempotency_key: crypto.randomUUID(),
            },
          })
        ).json()
      ).conversation;
      const path = `/api/assistant/conversations/${conversation.id}`;
      const workspace = await (await request.get(`${path}/workspace`, { headers })).json();
      const target = {
        schema: 'assistant-month-close-v1',
        company_id: book.company_id,
        book_id: book.id,
        period: '2026-10',
        inputs_fingerprint: 'a'.repeat(64),
      };
      workspace.messages.messages = [
        {
          id: 'synthetic-detail',
          turn_id: 'synthetic-turn',
          role: 'assistant',
          created_at: '2026-10-31T00:00:00Z',
          content: {
            answer: '本月核对尚有待处理明细，会计复核后导出。',
            citation_ids: [`book:${book.id}`],
            proposals: [],
          },
          citations: [
            {
              id: `book:${book.id}`,
              kind: 'book',
              book_id: book.id,
              label: '查看本月对账与月结',
              detail: '',
              delivery_target: target,
            },
          ],
        },
      ];
      await page.route(new RegExp(`${path}/workspace\\?`), (route) =>
        route.fulfill({ json: workspace }),
      );
      let reads = 0,
        writes = 0;
      let releaseLate: (() => void) | undefined;
      let lateStarted: (() => void) | undefined;
      const started = new Promise<void>((resolve) => {
        lateStarted = resolve;
      });
      const late = new Promise<void>((resolve) => {
        releaseLate = resolve;
      });
      await page.route(`**${path}/turns/synthetic-turn/delivery-details?*`, async (route) => {
        reads++;
        const url = new URL(route.request().url());
        const next = url.searchParams.has('after');
        const kind = url.searchParams.get('kind')!;
        if (next && fault === 'late') {
          lateStarted?.();
          await late;
        }
        if (next && ['stale', 'revoked', 'mfa', 'server'].includes(fault)) {
          await route.fulfill({
            status: fault === 'stale' ? 409 : fault === 'server' ? 500 : 403,
            json: {
              code: fault === 'mfa' ? 'admin_mfa_required' : undefined,
              detail: fault === 'mfa' ? '二次验证' : 'private internal details',
            },
          });
          return;
        }
        const start = next ? 10 : 0;
        const end = kind === 'control' ? 1 : next ? 12 : 10;
        await route.fulfill({
          json: {
            ...target,
            schema: 'assistant-delivery-page-v1',
            company_id: next && fault === 'wrong-scope' ? 'another-company' : book.company_id,
            conversation_id: conversation.id,
            turn_id: 'synthetic-turn',
            kind,
            limit: 10,
            start,
            end,
            total: kind === 'control' ? 1 : 12,
            has_more: kind === 'bank' && !next,
            cursor: `synthetic-cursor-${end}`,
            snapshot_fingerprint: 'b'.repeat(64),
            page_fingerprint: 'c'.repeat(64),
            accounting_approved: false,
            rows: Array.from({ length: end - start }, (_, i) => ({
              key: (start + i + 1).toString(16).padStart(64, '0'),
              label: kind === 'bank' ? `分页账户-${start + i + 1}` : '子账科目-2202',
              passed: false,
              issues: [
                {
                  code: kind === 'bank' ? 'bank_coverage_missing' : 'control_account_difference',
                  reason: kind === 'bank' ? '缺少已独立核验的完整对账单' : '账面与子账仍有差异',
                  owner_role: '账套会计',
                  next_action: '打开正式账簿核对原件与配置',
                  evidence_ids: [`book:${book.id}`],
                },
              ],
            })),
          },
        });
      });
      page.on('request', (req) => {
        if (
          ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method()) &&
          !req.url().includes('/session')
        )
          writes++;
      });
      await page.goto('/');
      await choosePersona(page, 'accountant');
      await page.getByRole('button', { name: '问会计助手', exact: true }).click();
      const drawer = page.getByRole('dialog', { name: '会计助手', exact: true });
      await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(book.company_id);
      await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
      await drawer.locator('.ga-history-row').filter({ hasText: conversation.title }).click();
      const panel = drawer.getByRole('region', { name: '诊断明细分页' });
      await expect(panel).toBeVisible();
      expect(reads).toBe(0);
      await panel.getByRole('button', { name: '继续查看核对明细' }).click();
      await expect(panel).toContainText('第 1–10 条 / 共 12 条');
      await panel.getByRole('button', { name: '下一页明细' }).click();
      await expect(panel.getByText('分页账户-1', { exact: true })).toHaveCount(0);
      if (fault === 'late') {
        await started;
        await panel.getByRole('button', { name: '子账科目', exact: true }).click();
        await expect(panel).toContainText('子账科目-2202');
        releaseLate?.();
        await expect(panel.getByText('分页账户-11', { exact: true })).toHaveCount(0);
      } else if (fault === 'revoked') {
        // Shared 403 handling clears protected views, including the entire
        // assistant drawer, without logging a still-authenticated user out.
        await expect(drawer).toHaveCount(0);
        await expect(page.getByRole('region', { name: '当前登录身份' })).toContainText(
          '权限待重新确认',
        );
        await expect(page.getByRole('heading', { name: '登录', exact: true })).toHaveCount(0);
        await expect(page.getByText('分页账户-1', { exact: true })).toHaveCount(0);
      } else if (fault !== 'none') {
        const messages = {
          stale: '依据已变化',
          revoked: '无权查看',
          'wrong-scope': '范围或版本不一致',
          mfa: '二次验证',
          server: '暂时无法读取',
        };
        await expect(panel.getByRole('alert')).toContainText(messages[fault]);
        await expect(panel.locator('.ga-detail-rows')).toHaveCount(0);
        await panel.getByRole('button', { name: '重试读取' }).click();
        await expect(panel.getByRole('alert')).toContainText(messages[fault]);
      } else {
        await expect(panel).toContainText('第 11–12 条 / 共 12 条');
        await expect(panel.getByRole('button', { name: '下一页明细' })).toHaveCount(0);
        await expect(panel).toContainText('不代表已复核、已关期或可交付');
        await panel.getByRole('button', { name: '收起核对明细' }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: `artifacts/assistant-pages-${width}.png`, fullPage: true });
        await panel.getByRole('button', { name: '回到第一页' }).click();
        await expect(panel).toContainText('第 1–10 条 / 共 12 条');
        await panel.getByRole('button', { name: '收起核对明细' }).click();
        await expect(panel.locator('.ga-detail-rows')).toHaveCount(0);
      }
      expect(writes).toBe(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  }
}
