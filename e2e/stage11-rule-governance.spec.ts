import { independentReviewer } from './independent-review';
import { openAccounting, openBookSettings } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('稳定企业规则去重、真实使用与人工修改在工作台可追溯', async ({ page, request }) => {
  test.setTimeout(60_000);
  const token = (await (await request.get('/api/session?persona=accountant')).json()).token;
  const headers = { Authorization: `Bearer ${token}` },
    marker = crypto.randomUUID().slice(0, 8),
    name = `第十一阶段规则 ${marker}`;
  const book = await (
    await request.post('/api/books', { headers, data: { name, period: '2026-10' } })
  ).json();
  const reviewer = await independentReviewer(request, headers, book.id);
  const entity = await (
    await request.post(`/api/books/${book.id}/ledger/entities`, {
      headers,
      data: { kind: 'counterparty', code: 'RULE-SUP', name: `正式供方 ${marker}` },
    })
  ).json();
  const base = `/api/books/${book.id}`,
    party = `正式供方 ${marker}`;
  async function sample(external: string, counterparty: string, code?: string, amount = '113.00') {
    const imported = await request.post(`${base}/imports`, {
      headers,
      data: {
        source_name: '明确虚构的规则治理验证',
        rows: [
          {
            external_id: external,
            date: '2026-10-03',
            description: `办公费用 ${external}`,
            counterparty,
            amount,
            direction: 'expense',
            evidence_ref: '明确虚构已核验归档票据',
          },
        ],
      },
    });
    expect(imported.status()).toBe(200);
    expect((await request.post(`${base}/suggestions`, { headers })).status()).toBe(200);
    const overview = await (await request.get(base, { headers })).json();
    const source = overview.sources.find(
      (item: { external_id: string }) => item.external_id === external,
    );
    let draft = overview.drafts.find((item: { source_id: string }) => item.source_id === source.id);
    if (code) {
      const changed = await request.patch(`${base}/drafts/${draft.id}`, {
        headers,
        data: {
          expected_version: draft.version,
          description: `会计核实用途 ${external}`,
          correction_reason: '真实用途独立核验（合成测试）',
          lines: [
            { account_code: code, debit: amount, credit: '0.00' },
            { account_code: '1002', debit: '0.00', credit: amount },
          ],
        },
      });
      expect(changed.status()).toBe(200);
      draft = await changed.json();
    }
    expect(
      (
        await request.post(`${base}/drafts/${draft.id}/approve`, {
          headers: reviewer,
          data: { expected_version: draft.version },
        })
      ).status(),
    ).toBe(200);
  }
  await sample(`p1-${marker}`, party, '6602.99');
  await sample(`p2-${marker}`, party, '6602.99', '31.00');
  await sample(`n-${marker}`, `另一供方 ${marker}`);
  let data = await (await request.get(`${base}/learning`, { headers })).json();
  expect(data.samples).toHaveLength(3);
  expect(data.rules).toHaveLength(1);
  const rule = data.rules[0],
    negative = data.samples.find((item: { counterparty: string }) => item.counterparty !== party);
  expect(rule.definition.conditions.counterparty_id).toBe(entity.id);
  expect(rule.sample_ids).toHaveLength(1);
  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(marker);
  await page.getByRole('listitem').filter({ hasText: name }).click();
  await openBookSettings(page);
  await page.getByRole('button', { name: '审核样例与规则', exact: true }).click();
  await expect(page.getByLabel('企业规则治理')).toContainText('有效往来方');
  await page.locator('.learning-rule-choice').filter({ hasText: party }).click();
  await page.getByRole('button', { name: '修订为待发布版本', exact: true }).click();
  await expect(page.getByLabel('规则稳定往来方')).toHaveValue(entity.id);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page
    .getByLabel(`选为反例 ${negative.counterparty} ${negative.id}`, { exact: true })
    .check();
  await page.getByRole('button', { name: '评估正例与 1 条反例', exact: true }).click();
  await expect(page.getByText('已通过当前样例评估', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '填写依据并发布', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '发布经评估的企业规则' });
  await dialog.getByLabel('规则发布依据').fill('合成正反例核验，限定本企业稳定往来方');
  await dialog.getByRole('button', { name: '确认发布', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await sample(`hit-${marker}`, party, '6602.03', '35.01');
  data = await (await request.get(`${base}/learning/governance`, { headers })).json();
  expect(
    data.usage.find((item: { rule_id: string }) => item.rule_id === rule.id).accountant_modified,
  ).toBe(1);
  await page.getByRole('button', { name: '读取最新状态', exact: true }).click();
  await expect(page.locator('.learning-usage')).toContainText('会计修改分录 1 张');
  await expect(page.getByLabel('企业规则治理')).toContainText('范围重叠');
  await page.screenshot({ path: 'artifacts/stage11-rule-governance.png', fullPage: true });
  const employee = await (await request.get('/api/session?persona=employee')).json();
  expect(
    (
      await request.get(`${base}/learning/governance`, {
        headers: { Authorization: `Bearer ${employee.token}` },
      })
    ).status(),
  ).toBe(404);
});
