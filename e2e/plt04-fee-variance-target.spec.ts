import { choosePersona } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('PLT04 规费差异待办定位、真实办理结案、返回原队列及无权/重复办理门禁', async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const employeeSession = await (await request.get('/api/session?persona=employee')).json();
  const employeeHeaders = { Authorization: `Bearer ${employeeSession.token}` };

  const created = await request.post('/api/books', {
    headers,
    data: { name: `PLT04 规费差异闭环 ${Date.now()}`, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const recorded = await request.post(`/api/tax/books/${book.id}/fee-variances`, {
    headers,
    data: {
      kind: 'unmatched',
      expected_cents: 0,
      actual_cents: 500,
      evidence_ref: '合成银行回单',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(recorded.status()).toBe(201);
  const variance = await recorded.json();

  await page.goto('/workspace/home');
  await choosePersona(page, 'accountant');
  const row = page.locator(`[data-task-key="fee_variance:${variance.id}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });

  // 1) 桌面与手机端均能准确定位目标行且零横向溢出，并能返回原待办
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await row.getByRole('button').last().click();
    const target = page.locator(`#fee-variance-${variance.id}`);
    await expect(target).toBeVisible({ timeout: 15_000 });
    await expect(target).toHaveClass(/is-target/);
    await expect(page.getByText('已定位到待办中的这笔差异。')).toBeVisible();
    await expect(page).toHaveURL(/period=2026-10/);
    await expect(page).toHaveURL(/company=[^&]+/);
    if (viewport.width === 390) {
      await expect(
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).resolves.toBe(true);
    }

    await page
      .getByRole('button', { name: /返回待办/ })
      .first()
      .click();
    await expect(page).toHaveURL(/\/workspace\/home/, { timeout: 15_000 });
    await expect(row).toBeVisible({ timeout: 15_000 });
  }

  // 2) 桌面端从待办进入目标行 -> 真实开始处理并保存核查依据 -> 返回原队列确认该事项已消账
  await page.setViewportSize({ width: 1366, height: 768 });
  await row.getByRole('button').last().click();
  const target = page.locator(`#fee-variance-${variance.id}`);
  await expect(target).toBeVisible({ timeout: 15_000 });
  const startBtn = target.getByRole('button', { name: '开始处理' });
  if (await startBtn.isVisible()) {
    await startBtn.click();
  }
  const reasonSelect = target.locator('.fee-variance-form select');
  const options = await reasonSelect.locator('option').evaluateAll((els) =>
    els.map((el) => (el as HTMLOptionElement).value),
  );
  const resolvedCode = options.find((v) => v && v !== 'unresolved') ?? 'unresolved';
  await reasonSelect.selectOption(resolvedCode);
  await target.getByLabel('核查依据').fill('合成平台回单已核清并登记处理');
  await target.getByRole('button', { name: '保存处理' }).click();
  await expect(target).not.toContainText('待处理', { timeout: 15_000 });

  await page
    .getByRole('button', { name: /返回待办/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/workspace\/home/, { timeout: 15_000 });
  await expect(row).toHaveCount(0, { timeout: 15_000 });

  // 3) 负向门禁：另一同事不得抢办已被认领的差异（409），无权员工不得访问（403/404）
  const reviewerSession = await (await request.get('/api/session?persona=reviewer')).json();
  const reviewerHeaders = { Authorization: `Bearer ${reviewerSession.token}` };
  const conflictStart = await request.post(`/api/tax/fee-variances/${variance.id}/start`, {
    headers: reviewerHeaders,
  });
  expect([403, 404, 409, 410]).toContain(conflictStart.status());

  const denied = await request.get(`/api/tax/books/${book.id}/fee-variances`, {
    headers: employeeHeaders,
  });
  expect([403, 404]).toContain(denied.status());
});
