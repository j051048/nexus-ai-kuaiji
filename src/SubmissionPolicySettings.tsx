import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { SubmissionPolicy } from './business-types';
import type { Session } from './types';

export function SubmissionPolicySettings({
  session,
  companyId,
  onChanged,
  onBusyChange,
}: {
  session: Session;
  companyId: string;
  onChanged: () => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const [policy, setPolicy] = useState<SubmissionPolicy | null>(null);
  const [mode, setMode] = useState<SubmissionPolicy['mode']>('department');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const scope = `${session.scope_key}:${companyId}`;
  const current = useRef(scope);
  current.current = scope;
  const locked = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    setPolicy(null);
    setError('');
    setNotice('');
    setReason('');
    if (companyId)
      void api<SubmissionPolicy>(
        `/companies/${encodeURIComponent(companyId)}/submission-policy`,
        session,
        { signal: controller.signal },
      )
        .then((value) => {
          if (!controller.signal.aborted) {
            setPolicy(value);
            setMode(value.mode);
          }
        })
        .catch((failure) => {
          if (!controller.signal.aborted) setError(errorMessage(failure));
        });
    return () => controller.abort();
  }, [session, companyId]);
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  async function save() {
    if (!policy || locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    const captured = scope;
    try {
      const result = await api<SubmissionPolicy>(
        `/companies/${encodeURIComponent(companyId)}/submission-policy`,
        session,
        { method: 'PUT', body: { mode, reason, expected_version: policy.version } },
      );
      if (current.current !== captured) return;
      setPolicy(result);
      setReason('');
      await onChanged();
      if (current.current === captured) setNotice('提交方式已保存，之后新建的事项使用此流程。');
    } catch (failure) {
      if (current.current === captured) setError(errorMessage(failure));
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  if (!companyId) return null;
  return (
    <section className="organization-department" aria-label="企业资料提交方式">
      <h3>资料提交方式</h3>
      <p className="field-help">
        按企业实际协作方式配置。已有事项保留创建时的流程，最终凭证由会计独立复核。
      </p>
      <label>
        <span className="field-label">提交路径</span>
        <select
          aria-label="企业资料提交路径"
          value={mode}
          disabled={busy || !policy}
          onChange={(event) => setMode(event.target.value as SubmissionPolicy['mode'])}
        >
          <option value="department">员工提交 → 部门审批 → 企业财务 → 会计</option>
          <option value="direct">老板／出纳提交 → 授权会计接收</option>
        </select>
      </label>
      {mode === 'direct' && (
        <p className="field-help">
          企业老板、管理员及出纳财务可上传，无需设置部门；对应月份的授权会计接收后生成待审草稿。
        </p>
      )}
      <label>
        <span className="field-label">变更依据</span>
        <input
          aria-label="提交方式变更依据"
          value={reason}
          maxLength={1000}
          disabled={busy || !policy}
          placeholder="例如：老板集中提供票据，由代账会计接收"
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <button
        type="button"
        className="button secondary"
        disabled={busy || !policy || !reason.trim()}
        onClick={() => void save()}
      >
        {busy ? '正在保存…' : '保存提交方式'}
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
