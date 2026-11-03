import { test, expect } from '@playwright/test';
import { choosePersona } from './ui-navigation';

// This suite uses real local API + identity + SQLite, no page.route mocks.
for (const width of [1280, 390]) {
  test(`P1 接手原记录、同人改期与未知态 ${width}`, async ({ page, request }, info) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/');
    await choosePersona(page, 'accountant');
    await page.goto('/workspace/monthly?period=2026-10');
    await page.getByRole('button', { name: '人工接手', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '人工接手', exact: true });
    const failed = dialog.locator('article').filter({ hasText: '批量项版本' });
    await expect(failed).toContainText('尚未绑定账套');
    await expect(failed.getByRole('button', { name: '选择负责人' })).toHaveCount(0);
    await failed.getByRole('button', { name: '打开原记录核对' }).click();
    await expect(page).toHaveURL(/handoff_id=item-n04/);
    const originalURL = page.url();
    await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toContainText(
      '缺少银行流水',
    );
    await expect(page.getByRole('button', { name: '只重试失败项', exact: true })).toBeDisabled();
    await page.reload();
    await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toContainText(
      '缺少银行流水',
    );
    expect(Object.fromEntries(new URL(page.url()).searchParams)).toEqual(
      Object.fromEntries(new URL(originalURL).searchParams),
    );
    await page.goBack();
    await expect(page.getByRole('region', { name: '接手原记录' })).toHaveCount(0);
    await page.goForward();
    await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toContainText(
      '缺少银行流水',
    );
    expect(Object.fromEntries(new URL(page.url()).searchParams)).toEqual(
      Object.fromEntries(new URL(originalURL).searchParams),
    );
    await page.screenshot({ path: info.outputPath(`batch-${width}.png`), fullPage: true });
    await page
      .getByRole('dialog', { name: '批量办理', exact: true })
      .getByRole('button', { name: '关闭弹窗', exact: true })
      .click();
    await expect(dialog).toBeVisible();
    const notification = dialog.locator('article').filter({ hasText: '通知版本' });
    await notification.getByRole('button', { name: /选择负责人|调整负责人或期限/ }).click();
    await dialog.getByLabel('接手人', { exact: true }).selectOption('local-accountant');
    const firstDate = width === 1280 ? '2026-11-10' : '2026-11-20';
    const nextDate = width === 1280 ? '2026-11-12' : '2026-11-22';
    await dialog.getByLabel('办理期限').fill(firstDate);
    await dialog.getByLabel('指派依据').fill('先核对发送结果');
    await dialog.getByRole('button', { name: '确认指派' }).click();
    await expect(notification).toContainText(firstDate);
    await notification.getByRole('button', { name: '调整负责人或期限' }).click();
    await expect(dialog.getByLabel('接手人', { exact: true })).toHaveValue('local-accountant');
    await dialog.getByLabel('办理期限').fill(nextDate);
    await dialog.getByLabel('指派依据').fill('保留同一负责人调整期限');
    await dialog.getByRole('button', { name: '确认指派' }).click();
    await expect(notification).toContainText(nextDate);
    await page.screenshot({ path: info.outputPath(`assignment-${width}.png`), fullPage: true });
    await notification.getByRole('button', { name: '打开原记录核对' }).click();
    await expect(page).toHaveURL(/handoff_id=notif-n04/);
    await expect(page.getByRole('region', { name: '接手原记录' })).toContainText('结果未知');
    await expect(page.getByRole('region', { name: '接手原记录' })).toContainText('不自动重发');
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const queue = await (
      await request.get('/api/handoff', { headers: { Authorization: `Bearer ${session.token}` } })
    ).json();
    expect(
      queue.items.find((item: { source: { id: string } }) => item.source.id === 'notif-n04').source
        .version,
    ).toBe(2);
    const wrong = new URL(originalURL);
    wrong.searchParams.set('handoff_fingerprint', 'f'.repeat(64));
    await page.goto(wrong.toString());
    await expect(page.getByRole('region', { name: '接手原记录' }).getByRole('alert')).toBeVisible();
    await expect(page.getByRole('dialog', { name: '批量办理', exact: true })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath(`stale-${width}.png`), fullPage: true });
  });
}
