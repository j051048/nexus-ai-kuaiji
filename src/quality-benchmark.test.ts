import { describe, expect, it } from 'vitest';
import { benchmarkCostLabel } from './QualityBenchmarkPanel';

describe('固定样本集费用显示保持权限与未知值', () => {
  it('未获授权时不展示任何费用数字', () => {
    expect(
      benchmarkCostLabel({
        measurement: 'not_authorized',
        observed_calls: null,
        known_micro_cny: null,
        unknown_calls: null,
      }),
    ).toBe('费用仅向企业预算管理人员展示');
  });
  it('缺少证据不当作零费用', () => {
    expect(
      benchmarkCostLabel({
        measurement: 'no_model_cost_evidence',
        observed_calls: 0,
        known_micro_cny: null,
        unknown_calls: 0,
      }),
    ).toBe('无模型费用证据');
    expect(
      benchmarkCostLabel({
        measurement: 'recorded_model_calls_reused_no_new_charge',
        observed_calls: 2,
        known_micro_cny: null,
        unknown_calls: 2,
      }),
    ).toContain('已知费用尚未采集');
  });
  it('区分确定性回放与原始模型调用费用', () => {
    expect(
      benchmarkCostLabel({
        measurement: 'deterministic_replay_no_model_call',
        observed_calls: 0,
        known_micro_cny: 0,
        unknown_calls: 0,
      }),
    ).toBe('确定性回放，不产生模型调用');
    expect(
      benchmarkCostLabel({
        measurement: 'recorded_model_calls_reused_no_new_charge',
        observed_calls: 3,
        known_micro_cny: 123456,
        unknown_calls: 1,
      }),
    ).toContain('¥0.1235');
    expect(
      benchmarkCostLabel({
        measurement: 'recorded_model_calls_reused_no_new_charge',
        observed_calls: null,
        known_micro_cny: null,
        unknown_calls: null,
      }),
    ).toContain('原调用数量待核定');
  });
  it('确认、估算与缺失记录分别显示，不把已知部分当成总费用', () => {
    const partial = benchmarkCostLabel({
      measurement: 'recorded_model_calls_reused_no_new_charge',
      observed_calls: 3,
      known_micro_cny: 200_000,
      unknown_calls: 1,
      confirmed_micro_cny: 100_000,
      estimated_micro_cny: 100_000,
      pending_calls: 1,
      complete: false,
    });
    expect(partial).toContain('已确认 ¥0.1000');
    expect(partial).toContain('另有估算 ¥0.1000（待核定）');
    expect(partial).toContain('1 次请求待核定');
    expect(partial).toContain('1 次缺费用证据');
    expect(partial).not.toContain('¥0.2000');
    expect(
      benchmarkCostLabel({
        measurement: 'recorded_model_calls_reused_no_new_charge',
        observed_calls: 0,
        known_micro_cny: 0,
        unknown_calls: 0,
        confirmed_micro_cny: null,
        estimated_micro_cny: null,
        complete: true,
        no_send_records: 2,
      }),
    ).toBe('2 次本地解析或缓存复用，模型调用费用为零');
  });
});
