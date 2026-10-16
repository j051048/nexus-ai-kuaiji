import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, Circle, LoaderCircle, RefreshCw } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import './onboarding.css';

export type OnboardingStep = {
  id: string;
  title: string;
  complete: boolean;
  required: boolean;
  detail: string;
  tab: 'company' | 'members' | 'departments' | 'books' | 'work' | 'ai';
};
export type CompanyOnboarding = {
  company_id: string;
  period: string;
  active: boolean;
  ready: boolean;
  steps: OnboardingStep[];
  updated_at: string;
  note: string;
};

export default function AdminOnboarding({
  session,
  companyId,
  onOpen,
}: {
  session: Session;
  companyId: string;
  onOpen: (tab: OnboardingStep['tab']) => void;
}) {
  const [period, setPeriod] = useState(() =>
    new Intl.DateTimeFormat('sv-SE', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
    }).format(new Date()),
  );
  const [data, setData] = useState<CompanyOnboarding | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const key = `${session.scope_key}:${companyId}:${period}`;
  const live = useRef(key);
  live.current = key;
  const dataKey = useRef('');
  useEffect(() => {
    const controller = new AbortController();
    const started = key;
    setData(null);
    setError('');
    setLoading(true);
    void api<CompanyOnboarding>(
      `/admin/companies/${encodeURIComponent(companyId)}/onboarding?` +
        new URLSearchParams({ period }),
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted && live.current === started) {
          if (value.company_id !== companyId || value.period !== period)
            throw new Error('开通清单范围不一致，请重新加载。');
          dataKey.current = started;
          setData(value);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted && live.current === started) {
          setData(null);
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && live.current === started) setLoading(false);
      });
    return () => controller.abort();
  }, [session, companyId, period, key, revision]);
  const visible = dataKey.current === key ? data : null;
  const required = visible?.steps.filter((step) => step.required) ?? [];
  const completed = required.filter((step) => step.complete).length;
  return (
    <div className="onboarding-panel">
      <header className="onboarding-heading">
        <div>
          <p className="eyebrow">CLIENT SETUP</p>
          <h2>把这家企业接入日常工作</h2>
          <p>按实际配置继续完成，刷新或下次登录都可以接着做。</p>
        </div>
        <label>
          检查月份
          <input
            type="month"
            aria-label="开通检查月份"
            min="2000-01"
            max="2099-12"
            value={period}
            onChange={(event) => {
              if (/^20\d{2}-(0[1-9]|1[0-2])$/.test(event.target.value))
                setPeriod(event.target.value);
            }}
          />
        </label>
        <button
          className="button secondary small"
          disabled={loading}
          onClick={() => setRevision((n) => n + 1)}
        >
          <RefreshCw size={15} />
          重新检查
        </button>
      </header>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <p className="loading-block">
          <LoaderCircle size={16} className="spinner" />
          正在检查当前开通条件…
        </p>
      ) : (
        visible && (
          <>
            <div className="onboarding-progress" role="status">
              <strong>
                {!visible.active
                  ? '企业已暂停或归档'
                  : visible.ready
                    ? '本月基础流程已验证'
                    : `已完成 ${completed} / ${required.length} 项必要检查`}
              </strong>
              <span>
                {!visible.active
                  ? '恢复企业服务后，重新检查开通条件。'
                  : visible.ready
                    ? '仍需逐月确认资料、权限与交付。'
                    : '完成配置后，使用明确标记的资料验证一笔完整流程。'}
              </span>
            </div>
            <ol className="onboarding-steps" aria-label="企业开通清单">
              {visible.steps.map((step) => (
                <li key={step.id} className={step.complete ? 'complete' : ''}>
                  <span className="onboarding-step-icon" aria-hidden="true">
                    {step.complete ? <Check size={17} /> : <Circle size={17} />}
                  </span>
                  <div>
                    <strong>
                      {step.title}
                      {!step.required && <small>可选</small>}
                    </strong>
                    <p>{step.detail}</p>
                    <span className="sr-only">{step.complete ? '已完成' : '待完成'}</span>
                  </div>
                  <button
                    className="button secondary small"
                    disabled={!visible.active}
                    onClick={() => onOpen(step.tab)}
                  >
                    {step.complete ? '查看' : '去完成'}
                    <ArrowRight size={14} />
                  </button>
                </li>
              ))}
            </ol>
            <p className="field-help">{visible.note}</p>
          </>
        )
      )}
    </div>
  );
}
