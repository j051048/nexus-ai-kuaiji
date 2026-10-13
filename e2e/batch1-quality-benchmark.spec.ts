import { expect, test } from '@playwright/test';

test('固定样本集在未知结果后重试不重复版本，回放比较不把未覆盖算通过', async ({
  page,
  request,
}) => {
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` },
    second = { Authorization: `Bearer ${finance.token}` };
  const company = (await (await request.get('/api/business/context', { headers: second })).json())
    .companies[0];
  const marker = crypto.randomUUID().slice(0, 8),
    party = `固定样本供应商 ${marker}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name: `固定样本合成账套 ${marker}`, company_id: company.id, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/ledger/entities`, {
        headers,
        data: { kind: 'counterparty', code: marker, name: party },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '明确合成固定样本',
          rows: [
            {
              external_id: marker,
              date: '2026-10-03',
              description: '办公用品',
              counterparty: party,
              amount: '9.00',
              direction: 'expense',
              evidence_ref: '合成归档索引',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const draft = (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts[0];
  expect(
    (
      await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
        headers,
        data: { expected_version: draft.version },
      })
    ).status(),
  ).toBe(200);
  const candidate = (await (await request.get(`/api/books/${book.id}/quality`, { headers })).json())
    .candidates[0];
  const standardResponse = await request.post(`/api/books/${book.id}/quality/standards`, {
    headers,
    data: {
      sample_id: candidate.id,
      reason: '合成凭据逐项核对',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(standardResponse.status()).toBe(201);
  const standard = await standardResponse.json();
  expect(
    (
      await request.post(`/api/books/${book.id}/quality/standards/${standard.id}/confirm`, {
        headers: second,
        data: { evidence_checked: true, reason: '第二位会计独立核对合成原件' },
      })
    ).status(),
  ).toBe(200);
  await page.goto(`/workspace/quality?book=${book.id}`);
  await page.getByLabel(`选择评测标准 ${party} 9.00`, { exact: true }).check();
  const panel = page.getByRole('region', { name: '固定样本集与版本升级检查' });
  await panel.getByLabel('固定样本集依据').fill('覆盖合成试点费用事项，固定回归范围');
  let firstRequest = true;
  await page.route(`**/api/books/${book.id}/quality/benchmarks`, async (route) => {
    if (route.request().method() === 'POST' && firstRequest) {
      firstRequest = false;
      await route.fetch();
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  await panel.getByRole('button', { name: '冻结选中 1 个标准' }).click();
  await expect(panel.getByRole('alert')).toContainText('无法连接服务');
  await panel.getByRole('button', { name: '冻结选中 1 个标准' }).click();
  await expect(panel.getByLabel('固定样本集版本')).not.toHaveValue('');
  const fixed = await (
    await request.get(`/api/books/${book.id}/quality/benchmarks`, { headers })
  ).json();
  expect(fixed.total).toBe(1);
  expect(fixed.benchmarks[0].version).toBe(1);
  await panel.getByRole('button', { name: '按上方配置回放整个固定样本集' }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/books/${book.id}/quality`, { headers })).json()).runs
          .length,
    )
    .toBe(1);
  await expect(panel.getByRole('button', { name: '按上方配置回放整个固定样本集' })).toBeEnabled();
  await panel.getByRole('button', { name: '按上方配置回放整个固定样本集' }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/books/${book.id}/quality`, { headers })).json()).runs
          .length,
    )
    .toBe(2);
  const runs = (await (await request.get(`/api/books/${book.id}/quality`, { headers })).json())
    .runs;
  await panel.getByLabel('基线评测运行').selectOption(runs[1].id);
  await panel.getByLabel('候选评测运行').selectOption(runs[0].id);
  await panel.getByLabel('升级检查最低样本数').fill('1');
  await panel.getByRole('button', { name: '检查候选版本' }).click();
  await expect(panel.getByRole('status')).toContainText('需要处理后重新评测');
  await expect(panel.getByRole('status')).toContainText('未覆盖 1');
  await expect(panel.getByRole('status')).toContainText('确定性回放，不产生模型调用');
  await panel.screenshot({ path: 'artifacts/batch1-quality-benchmark.png' });
  const after = await (
    await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers })
  ).json();
  expect(after.status).toBe('approved');
  expect(after.version).toBe(draft.version + 1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
