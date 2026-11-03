import { expect, it } from 'vitest';
import { acceptHandoffs, handoffOriginalHref } from './batch-operations';
import { readWorkspaceRoute, workspaceRouteUrl } from './workspace-route';
import { assertHandoffDigest } from './handoff-target';
import { createHash } from 'node:crypto';

it('第二次读取即使 attempt 相同，来源内容变化也不得接纳', async () => {
  const parts = ['batch_item', 'i', 'b', 2, 'failed', 'stamp'];
  const digest = createHash('sha256').update(parts.join('|')).digest('hex');
  await expect(assertHandoffDigest(digest, parts)).resolves.toBeUndefined();
  await expect(assertHandoffDigest(digest, [...parts.slice(0, -1), 'new'])).rejects.toThrow();
  await expect(assertHandoffDigest(digest, [...parts.slice(0, -1), undefined])).rejects.toThrow();
});

it('接手链接冻结来源种类、对象、版本和指纹，刷新不会退回最新列表', () => {
  const source = {
    kind: 'notification_investigation' as const,
    id: 'notice-old',
    version: 2,
    fingerprint: 'a'.repeat(64),
  };
  const scope = {
    book_id: 'b',
    company_id: 'c',
    period: '2026-11',
    company_name: '甲',
    book_name: '甲账',
  };
  const url = new URL(handoffOriginalHref({ source, scope }), 'http://localhost');
  expect(url.pathname).toBe('/workspace/monthly');
  for (const [key, value] of Object.entries(source))
    expect(url.searchParams.get(`handoff_${key}`)).toBe(String(value));
  const route = readWorkspaceRoute(url.pathname, url.search);
  const restored = new URL(workspaceRouteUrl(route, undefined, url.search), url.origin);
  expect(restored.searchParams.get('handoff_id')).toBe('notice-old');
  expect(workspaceRouteUrl({ ...route, companyId: 'other' }, undefined, url.search)).not.toContain(
    'handoff_',
  );
});

it('没有生成账套的失败项仍可见，但不伪造账套也不可指派', () => {
  const item = {
    source: { kind: 'batch_item', id: 'i', version: 1, fingerprint: 'b'.repeat(64) },
    scope: {
      book_id: null,
      book_name: '尚未建立账套',
      company_id: 'c',
      company_name: '甲',
      period: '2026-11',
      batch_id: 'batch',
    },
    role: 'ops',
    next_action: '打开原批次核对',
    can_assign: false,
    assignment_blocker: '需先完成开账',
    assignee_id: null,
    status: 'pending',
    due_on: null,
  };
  expect(acceptHandoffs({ items: [item] })).toEqual([item]);
  expect(() => acceptHandoffs({ items: [{ ...item, can_assign: true }] })).toThrow();
});
