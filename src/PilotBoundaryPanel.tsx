import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import type { Session } from './types';

type Dependency = {
  code: string;
  label: string;
  status: 'unresolved' | 'confirmed' | 'not_applicable';
  evidence_ref: string;
};

type Target = {
  id: string;
  version: number;
  status: string;
  authority_mode: string;
  config: Record<string, string>;
  fingerprint: string;
  dependencies: Dependency[];
};

type Readiness = {
  company_id: string;
  exists: boolean;
  active_profile_id: string | null;
  draft_profile_id: string | null;
  target?: Target;
  blockers: { code: string; label: string }[];
};

const DEFAULT_CODES: Dependency[] = [
  ['income_336_335', '336/335 行收入明细逐行参与/排除'],
  ['prior_period_residue', '旧期间残留行处理'],
  ['imbalance_832_19', '832.19 摊销不平来源'],
  ['rent_3550', '租赁收入 3,550 差额'],
  ['insurance_formula', '保险月摊算法与取整'],
  ['tax_base_5990', '5,990 计税基数出处'],
  ['tax_rounding', '税额分位差处理'],
  ['tplus_template', 'T+ 版本、模板与回执'],
  ['source_files', '完整月源文件与相邻期'],
  ['payroll_scope', '工资/社保数据范围'],
].map(([code, label]) => ({ code, label, status: 'unresolved', evidence_ref: '' }));

function failure(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403 || error.status === 404)
      return '当前身份不能修改该企业的试点边界，或授权已变化。';
    return `${error.message} 请刷新后重新核对，不会自动重试。`;
  }
  return '试点边界暂时无法读取，请稍后重试。';
}

export function PilotBoundaryPanel({
  session,
  companyId,
}: {
  session: Session;
  companyId: string;
}) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [mode, setMode] = useState<'internal' | 'tplus_voucher' | 'hybrid'>('internal');
  const [deps, setDeps] = useState<Dependency[]>(DEFAULT_CODES);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<{ id: string; fingerprint: string } | null>(null);

  const base = `/tax/pilot/companies/${encodeURIComponent(companyId)}`;
  const target = readiness?.target;
  const blockers = useMemo(() => deps.filter((d) => d.status === 'unresolved'), [deps]);
  const loadSequence = useRef(0);
  const dirtyRef = useRef(false);

  async function load(signal?: AbortSignal) {
    const sequence = ++loadSequence.current;
    setError('');
    try {
      const data = await scheduleSubpanel(
        () => api<Readiness>(`${base}/readiness`, session, { signal }),
        signal,
      );
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setReadiness(data);
      if (data.target && (!signal || !dirtyRef.current)) {
        setMode((data.target.authority_mode as typeof mode) || 'internal');
        setDeps(data.target.dependencies.length ? data.target.dependencies : DEFAULT_CODES);
      }
    } catch (e) {
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setError(failure(e));
    }
  }
  useEffect(() => {
    dirtyRef.current = false;
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      loadSequence.current += 1;
      controller.abort();
    };
  }, [base, session.scope_key]);

  function setDependency(code: string, patch: Partial<Dependency>) {
    dirtyRef.current = true;
    setDeps((current) => current.map((d) => (d.code === code ? { ...d, ...patch } : d)));
  }

  async function save() {
    setBusy('save');
    setError('');
    setNotice('');
    loadSequence.current += 1;
    try {
      await scheduleSubpanel(() =>
        api(`${base}/draft`, session, {
          method: 'POST',
          body: {
            expected_version: target?.version ?? 0,
            config: { authority_mode: mode },
            dependencies: deps.map(({ code, status, evidence_ref }) => ({
              code,
              status,
              evidence_ref,
            })),
          },
        }),
      );
      dirtyRef.current = false;
      setNotice('已保存试点草稿；确认前不会生成正式预览。');
      await load();
    } catch (e) {
      setError(failure(e));
    } finally {
      setBusy('');
    }
  }

  async function confirm() {
    if (!target || blockers.length) return;
    setBusy('confirm');
    setError('');
    setNotice('');
    loadSequence.current += 1;
    try {
      await scheduleSubpanel(() =>
        api(`${base}/confirm`, session, {
          method: 'POST',
          body: { expected_version: target.version },
        }),
      );
      setNotice('边界已由当前负责人确认。');
      await load();
    } catch (e) {
      setError(failure(e));
    } finally {
      setBusy('');
    }
  }

  async function makePreview() {
    setBusy('preview');
    setError('');
    try {
      const result = await scheduleSubpanel(() =>
        api<{ id: string; fingerprint: string }>(`${base}/preview`, session, {
          method: 'POST',
          body: {},
        }),
      );
      setPreview(result);
      setNotice('已生成绑定当前确认版本的预览。');
    } catch (e) {
      setError(failure(e));
    } finally {
      setBusy('');
    }
  }

  return (
    <details className="tax-pilot" aria-label="试点边界与客户资料">
      <summary>
        试点边界与客户资料 {readiness?.active_profile_id ? '（已确认）' : '（未确认）'}
      </summary>
      <p className="field-help">
        未决规则不能激活；确认后旧预览会失效。缺失资料保持待确认，系统不替客户签认。
      </p>
      {error && (
        <p role="alert" className="tax-alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="tax-notice">
          {notice}
        </p>
      )}
      <label>
        业务权威边界
        <select
          value={mode}
          onChange={(e) => {
            dirtyRef.current = true;
            setMode(e.target.value as typeof mode);
          }}
        >
          <option value="internal">内部核算，暂不交付 T+</option>
          <option value="tplus_voucher">交付 T+ 凭证（需模板）</option>
          <option value="hybrid">混合：内部核算 + T+ 凭证</option>
        </select>
      </label>
      <table className="tax-pilot-table">
        <thead>
          <tr>
            <th>资料/规则</th>
            <th>状态</th>
            <th>客户证据</th>
          </tr>
        </thead>
        <tbody>
          {deps.map((d) => (
            <tr key={d.code}>
              <td>{d.label}</td>
              <td>
                <select
                  aria-label={d.label}
                  value={d.status}
                  onChange={(e) =>
                    setDependency(d.code, { status: e.target.value as Dependency['status'] })
                  }
                >
                  <option value="unresolved">待确认</option>
                  <option value="confirmed">客户已确认</option>
                  <option value="not_applicable">不适用</option>
                </select>
              </td>
              <td>
                <input
                  aria-label={`${d.label} 证据`}
                  value={d.evidence_ref}
                  onChange={(e) => setDependency(d.code, { evidence_ref: e.target.value })}
                  placeholder="邮件/会议纪要/文件编号"
                  maxLength={500}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="tax-pilot-actions">
        <button className="button secondary" disabled={!!busy} onClick={save}>
          {busy === 'save' ? '正在保存…' : '保存草稿'}
        </button>
        <button
          className="button"
          disabled={!!busy || !!blockers.length || !target}
          onClick={confirm}
        >
          {busy === 'confirm' ? '正在确认…' : '确认当前边界'}
        </button>
        <button
          className="button secondary"
          disabled={!!busy || !readiness?.active_profile_id}
          onClick={makePreview}
        >
          {busy === 'preview' ? '正在生成…' : '生成绑定预览'}
        </button>
      </div>
      {blockers.length > 0 && (
        <p role="status">仍有 {blockers.length} 项客户资料待确认，不能确认边界或生成正式预览。</p>
      )}
      {preview && (
        <p role="status">
          预览 {preview.id.slice(0, 8)} 绑定指纹 {preview.fingerprint.slice(0, 12)}…
        </p>
      )}
    </details>
  );
}
