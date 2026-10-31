import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

// Browser contract fixture only. Real current-human confirmation and export
// are independently exercised with restricted PG roles and restored identities.
for (const width of [1440, 390]) {
  test(`旧稿当前补核两步与范围拒绝 ${width}`, async ({ page, request }) => {
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const response = await request.post('/api/books', {
      headers,
      data: { name: `旧稿补核-${crypto.randomUUID().slice(0, 8)}`, period: '2026-10' },
    });
    expect(response.ok()).toBe(true);
    const book = await response.json();
    let state = 0;
    const sent: string[] = [];
    await page.route(`**/api/books/${book.id}/legacy-reviews`, (route) =>
      route.fulfill({
        json: {
          book_id: state === 3 ? 'wrong-company-book' : book.id,
          items:
            state === 2
              ? []
              : [
                  {
                    kind: 'draft',
                    subject_id: 'synthetic-old-draft',
                    book_id: book.id,
                    source_version: 2,
                    policy_version: 0,
                    content_fingerprint: 'a'.repeat(64),
                    mode: 'independent',
                    can_prepare: true,
                    can_confirm: state === 1,
                    blocked_reason: null,
                    preparation:
                      state === 1
                        ? {
                            id: 'current-request',
                            maker_user_id: 'different-preparer',
                            reason: '今天承担当前版本责任',
                          }
                        : null,
                    content: { description: state === 3 ? '不得显示他企内容' : '本地虚构旧稿资料' },
                  },
                ],
        },
      }),
    );
    await page.route('**/api/books/*/legacy-reviews/draft/*/prepare', async (route) => {
      sent.push('prepare');
      expect(route.request().postDataJSON().expected_version).toBe(2);
      state = 1;
      await route.fulfill({ json: { id: 'current-request' } });
    });
    await page.route('**/api/legacy-reviews/current-request/confirm', async (route) => {
      sent.push('confirm');
      state = 2;
      await route.fulfill({ json: { proof_kind: 'current_recheck' } });
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto(
      `/workspace/formal-books?book=${book.id}&company=${book.company_id}&period=2026-10`,
    );
    await choosePersona(page, 'accountant');
    await page.goto(
      `/workspace/formal-books?book=${book.id}&company=${book.company_id}&period=2026-10`,
    );
    await expect(page.locator('.formal-books-heading')).toContainText(book.name);
    const summary = page.getByText('旧稿补核（保留原历史，重新人工核对当前版本）', { exact: true });
    await summary.click();
    const panel = page.getByRole('region', { name: '旧稿当前版本补核' });
    await panel.getByLabel('本次核对依据与理由（至少 3 字）').fill('今天核对原件与该版本内容');
    await panel.getByRole('button', { name: '核对内容并认领当前版本' }).click();
    await expect(panel.getByText(/等待人工复核/)).toBeVisible();
    expect(sent).toEqual(['prepare']);
    await page.screenshot({ path: `tmp_test/legacy-review-pending-${width}.png`, fullPage: true });
    await panel
      .getByLabel('本次核对依据与理由（至少 3 字）')
      .fill('由另一位事务所会计重新独立复核');
    await panel.getByRole('button', { name: '确认本次人工复核' }).click();
    await expect(panel.getByText('当前账套没有需要补核的旧稿。')).toBeVisible();
    expect(sent).toEqual(['prepare', 'confirm']);
    await page.screenshot({ path: `tmp_test/legacy-review-${width}.png`, fullPage: true });
    state = 3;
    await summary.click();
    await summary.click();
    await expect(panel.getByRole('alert')).toContainText('返回范围不一致');
    await expect(panel.getByText('不得显示他企内容')).toHaveCount(0);
  });
}
