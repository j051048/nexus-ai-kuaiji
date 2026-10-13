import { describe, expect, it } from 'vitest';
import {
  migrationAcceptanceCurrent,
  migrationActive,
  migrationArchiveOnlyAccount,
  migrationColumnError,
  migrationDateError,
  migrationFieldDisplay,
  migrationFields,
  migrationMoney,
  migrationNeedsColumnConfirmation,
  migrationPostingMappings,
  migrationReadableFields,
  migrationReadableLines,
  migrationStep,
  migrationUnsignedCents,
  monthBoundary,
  normalizedMigrationDetail,
  parseCsvHeaders,
  suggestMigrationColumns,
} from './legacy-migration-helpers';
import type { MigrationDetail, MigrationJob } from './legacy-migration-types';

describe('旧账接管范围与精确显示', () => {
  it('按会计日期生成整月边界，闰年和世纪不依赖浏览器时区', () => {
    expect(monthBoundary('2024-02', true)).toBe('2024-02-29');
    expect(monthBoundary('2100-02', true)).toBe('2100-02-28');
    expect(monthBoundary('2026-09')).toBe('2026-09-01');
    expect(monthBoundary('2026-09', true)).toBe('2026-09-30');
    expect(() => monthBoundary('2026-13', true)).toThrow();
  });
  it('历史必须紧接目标月，不能有重复期、空档、颠倒或非月首接管', () => {
    expect(migrationDateError('2026-01', '2026-09', '2026-10-01')).toBeNull();
    expect(migrationDateError('2026-10', '2026-09', '2026-10-01')).toContain('开始');
    expect(migrationDateError('2026-01', '2026-10', '2026-10-01')).toContain('重叠');
    expect(migrationDateError('2026-01', '2026-08', '2026-10-01')).toContain('空档');
    expect(migrationDateError('2026-01', '2026-09', '2026-10-02')).toContain('首日');
    expect(migrationDateError('2026-12', '2026-12', '2027-01-01')).toBeNull();
  });
  it('大金额、负红字和整数分显示不丢失精度，非规范格式不猜金额', () => {
    expect(migrationMoney('9007199254740993.07')).toBe('9,007,199,254,740,993.07');
    expect(migrationMoney('-100.3')).toBe('-100.30');
    expect(migrationMoney('-900719925474099307', true)).toBe('-9,007,199,254,740,993.07');
    expect(migrationMoney('1,000.33')).toBe('金额格式待核对');
    expect(migrationMoney('1e3')).toBe('金额格式待核对');
    expect(migrationMoney(0.1)).toBe('金额格式待核对');
    expect(migrationMoney(Number.MAX_SAFE_INTEGER + 1, true)).toBe('金额格式待核对');
    expect(migrationMoney(null)).toBe('—');
  });
  it('清偿分配十进制直接转换整数分，拒绝负数、指数与多余小数', () => {
    expect(migrationUnsignedCents('0.10')! + migrationUnsignedCents('0.20')!).toBe(
      migrationUnsignedCents('0.30'),
    );
    expect(migrationUnsignedCents('9007199254740993.07')).toBe(900719925474099307n);
    for (const value of ['', '-1', '1e3', '1,000.00', '1.001', '1'.repeat(31)])
      expect(migrationUnsignedCents(value)).toBeNull();
  });
});

describe('旧账 CSV 字段确认', () => {
  it('识别 BOM、引号、双引号转义和字段内逗号，拒绝未闭合表头', () => {
    expect(parseCsvHeaders('\uFEFF科目编码,"金额,元","原""名"\r\n1002,1,名称')).toEqual([
      '科目编码',
      '金额,元',
      '原"名',
    ]);
    expect(parseCsvHeaders('code,name')).toEqual(['code', 'name']);
    expect(() => parseCsvHeaders('code,"name\n')).toThrow();
  });
  it('重复候选表头不给出不明确列建议，凭证号不能替代原凭证 ID', () => {
    const suggestion = suggestMigrationColumns(
      ['日期', '凭证字', '凭证号', '科目编码', '借方金额', '借方金额', '贷方金额'],
      'vouchers',
    );
    expect(suggestion.columns.debit).toBeUndefined();
    expect(suggestion.columns.external_id).toBeUndefined();
    expect(migrationColumnError('vouchers', suggestion)).toContain('原凭证 ID');
  });
  it('列序号必须在 0–63 范围，同一列不能映射两次', () => {
    const mapping = suggestMigrationColumns(
      ['档案类型', '档案编码', '档案名称', '原始ID', '期间'],
      'auxiliaries',
    );
    // Explicit human choices can use exact source headers without relying on an alias suggestion.
    mapping.columns = { kind: 0, code: 1, name: 2, external_id: 3, period: 4 };
    expect(migrationColumnError('auxiliaries', mapping)).toBeNull();
    expect(
      migrationColumnError('auxiliaries', { ...mapping, columns: { ...mapping.columns, name: 1 } }),
    ).toContain('重复');
    expect(
      migrationColumnError('auxiliaries', {
        ...mapping,
        columns: { ...mapping.columns, name: 64 },
      }),
    ).toContain('无效');
  });
  it('借贷分列余额须完整对应四个余额字段，不能用净额口径混算', () => {
    const mapping = {
      columns: {
        external_id: 0,
        period: 1,
        account_code: 2,
        opening_debit: 3,
        opening_credit: 4,
        period_debit: 5,
        period_credit: 6,
        closing_debit: 7,
        closing_credit: 8,
        scope: 9,
      },
      date_format: 'ymd' as const,
      allow_grouping: false,
      balance_mode: 'split' as const,
    };
    expect(migrationColumnError('balances', mapping)).toBeNull();
    expect(migrationFields('balances', mapping).some((field) => field.key === 'opening')).toBe(
      false,
    );
    expect(
      migrationColumnError('balances', {
        ...mapping,
        columns: { ...mapping.columns, closing_credit: undefined } as unknown as Record<
          string,
          number
        >,
      }),
    ).toContain('期末贷方');
  });
});

describe('后台任务与冻结验收状态', () => {
  const detail: MigrationDetail = {
    project: {
      id: 'p',
      version: 1,
      state: 'draft',
      source_product: 'generic',
      product_version: 'synthetic',
      external_book_id: 'e',
      source_system_key: 's',
      history_start: '2026-09-01',
      history_end: '2026-09-30',
      cutover_date: '2026-10-01',
    },
    target: { book_id: 'b', period: '2026-10', company_name: '合成企业' },
    permissions: { read: true, manage: false, review: false, activate: false },
    files: [],
    jobs: [],
    mapping: { version: 0, accounts: [], auxiliaries: [] },
  };
  it('空映射版本不跳过上传，完成解析才进入映射', () => {
    expect(migrationStep(detail)).toBe(0);
    expect(
      migrationStep({ ...detail, jobs: [{ id: 'j', kind: 'parse', state: 'succeeded' }] }),
    ).toBe(2);
    expect(migrationStep({ ...detail, project: { ...detail.project, state: 'active' } })).toBe(4);
  });
  it('权限与状态保留服务端事实，旧版本校验不能显示为有效', () => {
    const value = normalizedMigrationDetail({
      ...detail,
      project: { ...detail.project, status: 'active' } as typeof detail.project,
      jobs: [
        {
          id: 'j',
          kind: '',
          state: '',
          phase: 'parse',
          status: 'running',
          processed_count: 7,
          record_count: 10,
          attempt_count: 2,
        },
      ],
      latest_validation: {
        id: 'v',
        state: 'validated',
        current: false,
        basis_token: 'a'.repeat(64),
        blocking_count: 0,
        warning_count: 1,
      },
    });
    expect(value.project.state).toBe('active');
    expect(value.permissions.manage).toBe(false);
    expect(value.jobs[0]).toMatchObject({
      kind: 'parse',
      state: 'running',
      progress_done: 7,
      progress_total: 10,
      attempt: 2,
    });
    expect(value.latest_validation?.state).toBe('stale');
  });
  it('仅待执行、运行和重试等待任务触发轮询', () => {
    for (const state of ['queued', 'running', 'retry_wait'])
      expect(migrationActive({ state } as MigrationJob)).toBe(true);
    for (const state of ['succeeded', 'failed', 'cancelled', 'interrupted'])
      expect(migrationActive({ state } as MigrationJob)).toBe(false);
  });
  it('旧验收留存但不能用于新报告，也不能跳过新一轮独立验收', () => {
    const value = {
      ...detail,
      latest_validation: {
        id: 'v-new',
        current: true,
        state: 'validated',
        basis_token: 'b'.repeat(64),
        blocking_count: 0,
        warning_count: 0,
      },
      acceptance: { id: 'accept-old', validation_id: 'v-old', basis_token: 'a'.repeat(64) },
    };
    expect(migrationAcceptanceCurrent(value)).toBe(false);
    expect(migrationStep(value)).toBe(3);
    expect(
      migrationAcceptanceCurrent({
        ...value,
        acceptance: { ...value.acceptance, validation_id: 'v-new', basis_token: 'b'.repeat(64) },
      }),
    ).toBe(true);
  });
  it('只有当前参与校验的表格须确认列，修订前原件和明确 JSON 包不阻断', () => {
    const file = { id: 'f', filename: '凭证.xlsx', category: 'vouchers' as const, size_bytes: 10 };
    expect(migrationNeedsColumnConfirmation(file)).toBe(true);
    expect(
      migrationNeedsColumnConfirmation({
        ...file,
        mapping: { columns: {}, date_format: 'ymd', allow_grouping: false, confirmed: true },
      }),
    ).toBe(false);
    expect(migrationNeedsColumnConfirmation({ ...file, superseded: true })).toBe(false);
    expect(migrationNeedsColumnConfirmation({ ...file, filename: 'schema-verified.json' })).toBe(
      false,
    );
  });
});

describe('会计可读的历史依据', () => {
  it('上级科目只归档，不发送可记账映射；原科目树和本页明细映射保留', () => {
    const records = [
      {
        kind: 'account',
        external_id: 'parent',
        payload: { code: '6602', name: '费用汇总', posting: false },
      },
      {
        kind: 'account',
        external_id: 'leaf',
        payload: { code: '6602.01', name: '费用明细', parent_code: '6602', posting: true },
      },
    ];
    const edits = [
      { source_code: '6602', target_code: '6602', action: 'create' as const },
      { source_code: '6602.01', target_code: '6602.01', action: 'use_existing' as const },
    ];
    const snapshot = JSON.stringify(records);
    expect(migrationArchiveOnlyAccount(records[0])).toBe(true);
    expect(migrationArchiveOnlyAccount(records[1])).toBe(false);
    expect(migrationPostingMappings(edits, records)).toEqual([edits[1]]);
    expect(JSON.stringify(records)).toBe(snapshot);
    expect(edits).toHaveLength(2);
  });
  it('只按明确的科目posting=false处理父项，不把未知标志或其他档案误判成父科目', () => {
    expect(migrationArchiveOnlyAccount({ kind: 'account', payload: { posting: 'false' } })).toBe(
      false,
    );
    expect(migrationArchiveOnlyAccount({ kind: 'account', payload: {} })).toBe(false);
    expect(migrationArchiveOnlyAccount({ kind: 'auxiliary', payload: { posting: false } })).toBe(
      false,
    );
  });
  it('明确状态与红字用中文展示，不把未知审核状态等同于已记账', () => {
    expect(migrationFieldDisplay('status', 'posted')).toBe('已记账');
    expect(migrationFieldDisplay('status', 'unposted')).toBe('未记账');
    expect(migrationFieldDisplay('status', 'void')).toBe('作废');
    expect(migrationFieldDisplay('original_status', '已审核')).toBe('已审核');
    expect(migrationFieldDisplay('is_reversal', false)).toBe('否');
    expect(migrationFieldDisplay('is_reversal', true)).toBe('是');
    expect(migrationFieldDisplay('is_reversal', 'false')).toBe('状态待核对');
  });
  it('主区选择中文事实，原文件指纹和定位留在原始内容中不被改写', () => {
    const payload = {
      date: '2026-09-30',
      period: '2026-09',
      currency: 'CNY',
      status: 'posted',
      is_reversal: false,
      _evidence: { sha256: 'f'.repeat(64), json_pointer: '/records/4' },
      lines: [{ ordinal: 1, account_code: '1002', debit: '0.00', credit: '100.00' }],
    };
    const before = JSON.stringify(payload);
    const fields = migrationReadableFields(payload);
    expect(fields).toContainEqual({ key: 'date', label: '凭证日期', text: '2026-09-30' });
    expect(fields).toContainEqual({ key: 'currency', label: '币种', text: '人民币' });
    expect(fields.some((field) => ['_evidence', 'lines'].includes(field.key))).toBe(false);
    expect(JSON.stringify(payload)).toBe(before);
  });
  it('凭证行保留序号、原科目、负金额和辅助编码，不推测缺失序号或接受浮点金额', () => {
    const lines = migrationReadableLines([
      {
        ordinal: 2,
        account_code: '6602',
        description: '冲销原费用',
        debit: '-0.30',
        credit: '0.00',
        auxiliaries: { counterparty: '旧往来001', project: '原项目A' },
      },
      { account_code: '1002', debit: 0.3, credit: '0.00' },
    ]);
    expect(lines[0]).toEqual({
      ordinal: '2',
      accountCode: '6602',
      description: '冲销原费用',
      debit: '-0.30',
      credit: '0.00',
      auxiliaries: [
        { label: '往来方', value: '旧往来001' },
        { label: '项目', value: '原项目A' },
      ],
    });
    expect(lines[1].ordinal).toBe('待核对');
    expect(lines[1].debit).toBe('金额格式待核对');
  });
});
