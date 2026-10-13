import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import type { QualityMode, QualityRun } from './quality-types';

interface Benchmark {
  id: string;
  name: string;
  version: number;
  standard_ids: string[];
  sample_count: number;
  stale: boolean;
}
interface CostEvidence {
  observed_calls: number | null;
  known_micro_cny: number | null;
  unknown_calls: number | null;
  measurement: string;
  confirmed_micro_cny?: number | null;
  estimated_micro_cny?: number | null;
  pending_calls?: number | null;
  complete?: boolean;
  scope_note?: string;
  processing_records?: number;
  no_send_records?: number;
}
interface Comparison {
  baseline: { id: string; settings: QualityRun['settings']; costs: CostEvidence };
  candidate: { id: string; settings: QualityRun['settings']; costs: CostEvidence };
  report: {
    eligible: boolean;
    blockers: string[];
    sample_count: number;
    regressions: number;
    counts: Record<string, number>;
    scope_note: string;
    domain?: 'journal' | 'invoice_fields';
    baseline_archived?: boolean;
    items: {
      standard_id: string;
      regressed: boolean;
      baseline_status: string;
      candidate_status: string;
      differences: unknown[];
    }[];
  };
}
export function benchmarkCostLabel(value: CostEvidence) {
  if (value.measurement === 'deterministic_replay_no_model_call')
    return '确定性回放，不产生模型调用';
  if (value.measurement === 'not_authorized') return '费用仅向企业预算管理人员展示';
  if (value.measurement === 'no_model_cost_evidence') return '无模型费用证据';
  if (
    value.observed_calls === 0 &&
    value.no_send_records &&
    value.complete &&
    value.known_micro_cny === 0
  )
    return `${value.no_send_records} 次本地解析或缓存复用，模型调用费用为零`;
  if (value.confirmed_micro_cny !== undefined || value.estimated_micro_cny !== undefined) {
    const confirmed =
      value.confirmed_micro_cny === null || value.confirmed_micro_cny === undefined
        ? '已确认费用尚未采集'
        : `已确认 ¥${((value.confirmed_micro_cny ?? 0) / 1_000_000).toFixed(4)}`;
    const estimated = value.estimated_micro_cny
      ? ` · 另有估算 ¥${(value.estimated_micro_cny / 1_000_000).toFixed(4)}（待核定）`
      : '';
    const pending = value.pending_calls ? ` · ${value.pending_calls} 次请求待核定` : '';
    const unknown = value.unknown_calls ? ` · ${value.unknown_calls} 次缺费用证据` : '';
    const local = value.no_send_records ? ` · ${value.no_send_records} 次本地或缓存复用` : '';
    return `${confirmed}${estimated}${pending}${unknown}${local}`;
  }
  const known =
    value.known_micro_cny === null
      ? '已知费用尚未采集'
      : `已记录 ¥${(value.known_micro_cny / 1_000_000).toFixed(4)}`;
  const calls =
    value.observed_calls === null ? '原调用数量待核定' : `${value.observed_calls} 次原调用`;
  return `${known} · ${calls}${value.unknown_calls ? ` · ${value.unknown_calls} 次费用待核定` : ''}`;
}

export function QualityBenchmarkPanel({
  session,
  bookId,
  selected,
  runs,
  mode,
  model,
  promptVersion,
  onRun,
  onBusyChange,
  externalBusy = false,
}: {
  session: Session;
  bookId: string;
  selected: string[];
  runs: { id: string; settings: { mode: QualityMode } }[];
  mode: QualityMode;
  model: string;
  promptVersion: string;
  onRun: (run: QualityRun) => void;
  onBusyChange?: (busy: boolean) => void;
  externalBusy?: boolean;
}) {
  const [loaded, setLoaded] = useState<{ scope: string; sets: Benchmark[] } | null>(null),
    [chosen, setChosen] = useState('');
  const [name, setName] = useState('试点月度样本'),
    [reason, setReason] = useState('');
  const [baseline, setBaseline] = useState(''),
    [candidate, setCandidate] = useState('');
  const [minimum, setMinimum] = useState(10),
    [operationBusy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [loading, setLoading] = useState(true),
    [revoked, setRevoked] = useState(false);
  const [storedComparison, setComparison] = useState<{ scope: string; value: Comparison } | null>(
    null,
  );
  const active = useRef(true),
    lock = useRef(false),
    epoch = useRef(0),
    readEpoch = useRef(0);
  const requestKeys = useRef(new Map<string, { signature: string; key: string }>());
  const path = `/books/${encodeURIComponent(bookId)}/quality`;
  const scope = `${session.scope_key}:${bookId}`,
    current = useRef(scope);
  current.current = scope;
  const sets = loaded?.scope === scope && !revoked ? loaded.sets : [];
  const chosenSet = sets.find((item) => item.id === chosen);
  const comparison = storedComparison?.scope === scope && !revoked ? storedComparison.value : null;
  const busy = operationBusy || externalBusy || loading || revoked || loaded?.scope !== scope;
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const captured = scope,
        read = ++readEpoch.current;
      setLoading(true);
      try {
        const value = await api<{ benchmarks: Benchmark[] }>(
          `${path}/benchmarks?limit=100`,
          session,
          { signal },
        );
        if (
          active.current &&
          !signal?.aborted &&
          current.current === captured &&
          read === readEpoch.current
        )
          setLoaded({ scope: captured, sets: value.benchmarks });
      } finally {
        if (
          active.current &&
          !signal?.aborted &&
          current.current === captured &&
          read === readEpoch.current
        )
          setLoading(false);
      }
    },
    [path, scope, session],
  );
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    epoch.current++;
    lock.current = false;
    requestKeys.current.clear();
    setLoaded(null);
    setChosen('');
    setComparison(null);
    setBaseline('');
    setCandidate('');
    setError('');
    setReason('');
    setBusy(false);
    setRevoked(false);
    void load(controller.signal).catch((failure) => {
      if (!controller.signal.aborted && current.current === scope) setError(errorMessage(failure));
    });
    return () => {
      active.current = false;
      epoch.current++;
      readEpoch.current++;
      controller.abort();
    };
  }, [load, scope]);
  useEffect(() => {
    const revoke = (event: Event) => {
      const eventScope = (event as CustomEvent<string>).detail;
      if (eventScope && eventScope !== session.scope_key) return;
      epoch.current++;
      readEpoch.current++;
      lock.current = false;
      requestKeys.current.clear();
      setLoaded(null);
      setChosen('');
      setComparison(null);
      setBaseline('');
      setCandidate('');
      setBusy(false);
      setLoading(false);
      setRevoked(true);
      setError('当前授权已更新，请返回工作台重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange?.(operationBusy);
    return () => onBusyChange?.(false);
  }, [operationBusy, onBusyChange]);
  function requestKey(kind: string, body: unknown) {
    const signature = JSON.stringify([scope, body]);
    if (requestKeys.current.get(kind)?.signature !== signature)
      requestKeys.current.set(kind, { signature, key: crypto.randomUUID() });
    return requestKeys.current.get(kind)!.key;
  }
  async function command(action: (valid: () => boolean) => Promise<void>) {
    if (lock.current || busy) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setComparison(null);
    const captured = scope,
      operation = ++epoch.current;
    const valid = () =>
      active.current && current.current === captured && operation === epoch.current;
    try {
      await action(valid);
    } catch (failure) {
      if (valid()) setError(errorMessage(failure));
    } finally {
      if (valid()) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <section className="quality-card" aria-label="固定样本集与版本升级检查">
      <h2>固定样本集 · 比较配置版本</h2>
      <p>
        同一批独立确认的标准用于比较，样本新增时创建新版本。未覆盖、证据失效及退步项会阻止通过检查。
      </p>
      {loading && <p role="status">正在读取当前账套的固定样本集…</p>}
      <div className="quality-run-controls">
        <label>
          样本集名称
          <input
            aria-label="固定样本集名称"
            value={name}
            maxLength={100}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          冻结依据
          <input
            aria-label="固定样本集依据"
            value={reason}
            maxLength={1000}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            placeholder="例如：覆盖试点本月主要费用及收入类型"
          />
        </label>
        <button
          className="button secondary"
          disabled={busy || !selected.length || !name.trim() || !reason.trim()}
          onClick={() =>
            void command(async (valid) => {
              const body = {
                name: name.trim(),
                reason: reason.trim(),
                standard_ids: [...new Set(selected)].sort(),
              };
              const value = await api<Benchmark>(`${path}/benchmarks`, session, {
                method: 'POST',
                body: { ...body, idempotency_key: requestKey('freeze', body) },
              });
              if (!valid()) return;
              requestKeys.current.delete('freeze');
              setLoaded((old) => ({
                scope,
                sets: [
                  value,
                  ...(old?.scope === scope ? old.sets.filter((row) => row.id !== value.id) : []),
                ],
              }));
              setChosen(value.id);
              setReason('');
              await load();
            })
          }
        >
          冻结选中 {selected.length} 个标准
        </button>
      </div>
      <div className="quality-run-controls">
        <label>
          固定版本
          <select
            aria-label="固定样本集版本"
            value={chosen}
            disabled={busy}
            onChange={(event) => {
              setChosen(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">选择样本集</option>
            {sets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} v{item.version} · {item.sample_count} 项
                {item.stale ? ' · 证据已变化' : ''}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary"
          disabled={
            busy ||
            !chosenSet ||
            chosenSet.stale ||
            (mode.startsWith('stored_') && (!model.trim() || !promptVersion.trim()))
          }
          onClick={() =>
            void command(async (valid) => {
              const body = {
                mode,
                standard_ids: chosenSet!.standard_ids,
                ...(mode.startsWith('stored_')
                  ? { model: model.trim(), prompt_version: promptVersion.trim() }
                  : {}),
              };
              const value = await api<QualityRun>(`${path}/runs`, session, {
                method: 'POST',
                body: { ...body, idempotency_key: requestKey('run', body) },
              });
              if (valid()) {
                requestKeys.current.delete('run');
                onRun(value);
              }
            })
          }
        >
          按上方配置回放整个固定样本集
        </button>
      </div>
      <div className="quality-run-controls">
        <label>
          基线运行
          <select
            aria-label="基线评测运行"
            value={baseline}
            disabled={busy}
            onChange={(event) => {
              setBaseline(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">选择已有运行</option>
            {runs.map((row) => (
              <option key={row.id} value={row.id}>
                {row.id.slice(0, 8)} · {row.settings.mode}
              </option>
            ))}
          </select>
        </label>
        <label>
          候选运行
          <select
            aria-label="候选评测运行"
            value={candidate}
            disabled={busy}
            onChange={(event) => {
              setCandidate(event.target.value);
              setComparison(null);
            }}
          >
            <option value="">选择已有运行</option>
            {runs.map((row) => (
              <option key={row.id} value={row.id}>
                {row.id.slice(0, 8)} · {row.settings.mode}
              </option>
            ))}
          </select>
        </label>
        <label>
          最低样本数
          <input
            aria-label="升级检查最低样本数"
            type="number"
            min={1}
            max={100}
            value={minimum}
            disabled={busy}
            onChange={(event) => {
              setMinimum(Number(event.target.value));
              setComparison(null);
            }}
          />
        </label>
        <button
          className="button primary"
          disabled={
            busy ||
            !chosenSet ||
            chosenSet.stale ||
            !runs.some((row) => row.id === baseline) ||
            !runs.some((row) => row.id === candidate) ||
            baseline === candidate ||
            !Number.isInteger(minimum) ||
            minimum < 1 ||
            minimum > 100
          }
          onClick={() =>
            void command(async (valid) => {
              const value = await api<Comparison>(
                `${path}/benchmarks/${encodeURIComponent(chosen)}/compare`,
                session,
                {
                  method: 'POST',
                  body: {
                    baseline_run_id: baseline,
                    candidate_run_id: candidate,
                    minimum_samples: minimum,
                    allowed_differences: 0,
                  },
                },
              );
              if (valid()) setComparison({ scope, value });
            })
          }
        >
          {operationBusy ? '正在核对…' : '检查候选版本'}
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
          {!revoked && (
            <button
              type="button"
              className="text-button"
              disabled={operationBusy || externalBusy || loading}
              onClick={() => {
                setError('');
                void load().catch((failure) => {
                  if (active.current && current.current === scope) setError(errorMessage(failure));
                });
              }}
            >
              重新读取样本集
            </button>
          )}
        </p>
      )}
      {comparison && (
        <div className="quality-result" role="status">
          <h3>{comparison.report.eligible ? '已测范围通过升级检查' : '需要处理后重新评测'}</h3>
          <p>
            {comparison.report.domain === 'invoice_fields' ? '票据字段' : '分录处理'} ·{' '}
            {comparison.report.sample_count} 项 · 一致 {comparison.report.counts.passed} · 差异{' '}
            {comparison.report.counts.different} · 未覆盖 {comparison.report.counts.uncovered} ·
            失效 {comparison.report.counts.stale} · 退步 {comparison.report.regressions}
          </p>
          {comparison.report.baseline_archived && (
            <p>基线使用已归档配置；候选版本仍须按当前配置与有效证据检查。</p>
          )}
          {comparison.report.blockers.map((item) => (
            <p key={item}>{item}</p>
          ))}
          <p>基线费用：{benchmarkCostLabel(comparison.baseline.costs)}</p>
          <p>候选费用：{benchmarkCostLabel(comparison.candidate.costs)}</p>
          <p>{comparison.candidate.costs.scope_note}</p>
          <p>{comparison.report.scope_note}</p>
          <details>
            <summary>逐项比较</summary>
            <pre>{JSON.stringify(comparison.report.items, null, 2)}</pre>
          </details>
        </div>
      )}
      <p className="quality-note">
        模型回放读取已归档结果，当前页面不发起付费模型调用；比较报告不会自动发布配置或审核凭证。
      </p>
    </section>
  );
}
