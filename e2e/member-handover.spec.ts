import { expect, test } from '@playwright/test';
import { openManagement } from './ui-navigation';

test.setTimeout(120_000);

test('工作交接向导：把离职会计的未结束账套职责受审计转派给接替人', async ({ page, request }) => {
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` };
  const tag = crypto.randomUUID().slice(0, 8);
  const company = await (
    await request.post('/api/companies', {
      headers,
      data: { name: `交接企业 ${tag}`, tax_id: '' },
    })
  ).json();
  const book = await (
    await request.post('/api/books', {
      headers,
      data: { name: `交接账套 ${tag}`, company_id: company.id, period: '2026-10' },
    })
  ).json();
  try {
    expect(
      (
        await request.put('/api/firm/members', {
          headers,
          data: { email: 'employee@demo.example.invalid', role: 'operator' },
        })
      ).status(),
    ).toBe(200);

    expect(
      (
        await request.put(`/api/books/${book.id}/members/local-business-employee`, {
          headers,
          data: { role: 'editor' },
        })
      ).status(),
    ).toBe(200);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await openManagement(page);
    await page.getByRole('button', { name: '企业与人员管理', exact: true }).click();
    await page.getByRole('button', { name: '事务所人员', exact: true }).click();
    await expect(page.getByText('employee@demo.example.invalid')).toBeVisible();

    await page
      .getByRole('button', { name: '工作交接 employee@demo.example.invalid', exact: true })
      .click();
    const dialog = page.getByRole('dialog', { name: '工作交接', exact: true });
    await dialog.getByLabel('交接接替人').selectOption('local-reviewer');
    await dialog.getByRole('button', { name: '预览交接范围', exact: true }).click();
    await expect(dialog.getByText('待交接：账套职责 1 项')).toBeVisible();
    await expect(dialog.getByText('待处理任务 0 项')).toBeVisible();
    await dialog.screenshot({ path: 'artifacts/member-handover-preview.png' });
    await dialog.getByLabel('交接操作依据').fill('合成验证：离职会计未结束职责转派');
    await dialog.getByRole('button', { name: '执行交接', exact: true }).click();
    await expect(dialog.getByText('已完成交接：账套职责 1 项')).toBeVisible();
    await dialog.screenshot({ path: 'artifacts/member-handover-done.png' });
    await dialog.getByRole('button', { name: '完成交接', exact: true }).click();
    await expect(dialog).toHaveCount(0);

    const members = await (await request.get(`/api/books/${book.id}/members`, { headers })).json();
    const roles = Object.fromEntries(
      members.members.map((member: { user_id: string; role: string }) => [
        member.user_id,
        member.role,
      ]),
    );
    expect(roles['local-business-employee']).toBeUndefined();
    expect(roles['local-reviewer']).toBe('editor');
  } finally {
    expect(
      (await request.delete('/api/firm/members/local-business-employee', { headers })).ok(),
    ).toBe(true);
  }
});
