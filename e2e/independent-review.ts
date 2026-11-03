import { expect, type APIRequestContext, type Page } from '@playwright/test';

/** A real firm reviewer, never a promotion of the shared client identities. */
export async function independentReviewer(
  request: APIRequestContext,
  owner: { Authorization: string },
  bookId: string,
) {
  const session = await request.get('/api/session?persona=reviewer');
  expect(session.ok()).toBe(true);
  const { token } = await session.json();
  const grant = await request.put(`/api/books/${bookId}/members/local-reviewer`, {
    headers: owner,
    data: { role: 'reviewer' },
  });
  expect(grant.status(), await grant.text()).toBe(200);
  return { Authorization: `Bearer ${token}` };
}

export async function pinReviewer(page: Page) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'reviewer');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}
