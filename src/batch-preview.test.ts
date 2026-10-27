import { describe, expect, it } from 'vitest';
import {
  previewLines,
  previewVerdict,
  validateBatchPreview,
  type BatchPreview,
} from './batch-preview';

const preview: BatchPreview = {
  period: '2026-11',
  ready: 2,
  skipped: 1,
  blocked: 1,
  results: [
    { company_id: 'a', company_name: '第一客户', status: 'ready', from_period: '2026-10' },
    {
      company_id: 'b',
      company_name: '第二客户',
      status: 'skipped',
      code: 'already_open',
      detail: '该月份账套已存在',
    },
    {
      company_id: 'c',
      company_name: '第三客户',
      status: 'blocked',
      code: 'no_history',
      detail: '没有历史账期，请先完成首次接入',
    },
  ],
  notes: ['预检只读'],
};

describe('batch preview', () => {
  it('shows only the rows that will not run, in operator language', () => {
    const lines = previewLines(preview);
    expect(lines.map((line) => line.key)).toEqual(['b', 'c']);
    expect(lines[0]).toMatchObject({ tone: 'skipped', statusText: '该月份账套已存在' });
    expect(lines[1]).toMatchObject({
      tone: 'blocked',
      statusText: '没有历史账期，请先完成首次接入',
    });
  });

  it('keeps unknown codes verbatim', () => {
    const [line] = previewLines({
      ...preview,
      results: [{ company_id: 'z', company_name: 'Z', status: 'blocked', code: 'new_code' }],
    });
    expect(line.statusText).toBe('需先处理');
  });

  it('gates execution on the server-computed counts', () => {
    expect(previewVerdict(null).canRun).toBe(false);
    expect(previewVerdict(preview)).toMatchObject({ canRun: true });
    expect(previewVerdict({ ...preview, ready: 0 }).canRun).toBe(false);
  });

  it('rejects malformed previews instead of showing invented numbers', () => {
    expect(validateBatchPreview(preview)).toBe(true);
    expect(validateBatchPreview({ ...preview, period: '2026-13' })).toBe(false);
    expect(validateBatchPreview({ ...preview, results: 'nope' })).toBe(false);
    expect(validateBatchPreview(null)).toBe(false);
  });
});
