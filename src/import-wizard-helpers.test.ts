import { describe, expect, it } from 'vitest';
import {
  changeAmountMode,
  compatibleProfile,
  mappingError,
  mappingFields,
} from './import-wizard-helpers';
import type { ImportMapping, ImportProfile } from './import-wizard-types';

const mapping: ImportMapping = {
  columns: { date: 0, description: 1, amount: 2, direction: 3 },
  amount_mode: 'direction',
  date_format: 'ymd',
  allow_grouping: false,
};
describe('中文导入字段确认', () => {
  it('不能遗漏日期、方向或金额，且不能把同一列映射两次', () => {
    expect(mappingError(mapping, ['日期', '摘要', '金额', '方向'])).toBeNull();
    expect(
      mappingError({ ...mapping, columns: { date: 0, amount: 2 } }, [
        '日期',
        '摘要',
        '金额',
        '方向',
      ]),
    ).toContain('摘要');
    expect(
      mappingError({ ...mapping, columns: { ...mapping.columns, direction: 2 } }, [
        '日期',
        '摘要',
        '金额',
        '方向',
      ]),
    ).toContain('一列');
    expect(
      mappingError({ ...mapping, columns: { ...mapping.columns, direction: 8 } }, [
        '日期',
        '摘要',
        '金额',
        '方向',
      ]),
    ).toContain('范围');
  });
  it('切换分列模式移除已不适用的金额和方向，但保留原始身份列', () => {
    const next = changeAmountMode(
      { ...mapping, columns: { ...mapping.columns, external_id: 4 } },
      'split',
    );
    expect(next.columns).toEqual({ date: 0, description: 1, external_id: 4 });
    expect(mappingFields('split')).toContain('income');
    expect(mappingError(next, ['日期', '摘要', '金额', '方向', '编号'])).toContain('收入金额');
  });
  it('保存的格式仅在表头顺序完全一致时复用，不按相似列名猜测', () => {
    const profile: ImportProfile = {
      id: 'p',
      namespace: 'bank',
      bank_account: '1234',
      headers: ['日期', '摘要', '金额', '方向'],
      mapping,
      version: 1,
    };
    expect(compatibleProfile(profile, ['日期', '摘要', '金额', '方向'])).toBe(true);
    expect(compatibleProfile(profile, ['日期', '摘要', '方向', '金额'])).toBe(false);
    expect(compatibleProfile(profile, ['日期', '摘要', '金额'])).toBe(false);
  });
});
