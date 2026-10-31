import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
for (const width of [1440, 390]) {
  for (const fault of ['none', 'revoked', 'wrong-company'] as const) {
    test(`交付诊断明细与当前月结入口 ${width} ${fault}`, async ({ page, request }) => {
      await page.setViewportSize({ width, height: 900 });
      const session = await (await request.get('/api/session?persona=accountant')).json();
      const headers = { Authorization: `Bearer ${session.token}` };
      const created = await request.post('/api/books', {
        headers,
        data: { name: `明细-${crypto.randomUUID()}`, period: '2026-10' },
      });
      expect(created.status()).toBe(201);
      const book = await created.json();
      const conversation = (
        await (
          await request.post('/api/assistant/conversations', {
            headers,
            data: {
              company_id: book.company_id,
              book_ids: [book.id],
              title: `诊断-${crypto.randomUUID()}`,
              idempotency_key: crypto.randomUUID(),
            },
          })
        ).json()
      ).conversation;
      const path = `/api/assistant/conversations/${conversation.id}`;
      const workspace = await (await request.get(`${path}/workspace`, { headers })).json();
      workspace.messages.messages = [
        {
          id: 'synthetic-detail',
          role: 'assistant',
          created_at: '2026-10-31T00:00:00Z',
          input_fingerprint: 'historical-synthetic',
          content: {
            answer:
              '2026-10 月交付核对：科目 2202 应付账款差额 -10000.00 元（借方为正）。银行账户缺少已独立核验对账单。负责岗位：账套会计与独立核验人。会计复核后导出。',
            citation_ids: [`book:${book.id}`],
            proposals: [],
          },
          citations: [
            {
              id: `book:${book.id}`,
              kind: 'book',
              book_id: book.id,
              label: '查看本月对账与月结',
              detail: '只打开当前资料',
              delivery_target: {
                schema: 'assistant-month-close-v1',
                company_id: fault === 'wrong-company' ? 'other-company' : book.company_id,
                book_id: book.id,
                period: '2026-10',
                inputs_fingerprint: 'a'.repeat(64),
              },
            },
          ],
        },
      ];
      await page.route(new RegExp(`${path}/workspace\\?`), (route) =>
        route.fulfill({ json: workspace }),
      );
      let writes = 0;
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
      await expect(drawer.locator('.ga-message.assistant')).toContainText('-10000.00');
      await expect(drawer.locator('.ga-historical-citation')).toBeVisible();
      if (fault === 'none')
        await page.screenshot({ path: `artifacts/assistant-details-${width}.png`, fullPage: true });
      if (fault === 'revoked')
        await page.route(`**/api/books/${book.id}`, (route) =>
          route.fulfill({ status: 403, json: { detail: '当前账套授权已撤销' } }),
        );
      await drawer.getByRole('button', { name: '查看本月对账与月结', exact: true }).click();
      if (fault === 'none') {
        await expect(page).toHaveURL(new RegExp(`/workspace/formal-books\\?[^#]*book=${book.id}`));
        await expect(page.getByRole('heading', { name: '正式账簿', exact: true })).toBeVisible();
        await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
        await expect(
          page.getByRole('heading', { name: '正式月结与报表发布', exact: true }),
        ).toBeVisible();
        await page.screenshot({
          path: `artifacts/assistant-details-target-${width}.png`,
          fullPage: true,
        });
      } else {
        await expect(page.getByRole('heading', { name: '正式账簿', exact: true })).toHaveCount(0);
        await expect(page.getByRole('alert')).toContainText(
          fault === 'revoked' ? '撤销' : '范围或权限已变化',
        );
      }
      expect(writes).toBe(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    });
  }
}
