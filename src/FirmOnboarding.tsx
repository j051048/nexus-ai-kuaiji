import { useEffect, useState } from 'react';
import { ArrowRight, Building2, Copy, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-onboarding.css';

type Company = { id: string; name: string; tax_id?: string; status: string };
type Step = {
  id: string;
  title: string;
  complete: boolean;
  required: boolean;
  detail: string;
};
type Preview = {
  company_id: string;
  period: string;
  ready: boolean;
  missing_steps: string[];
  blockers: string[];
  target_book_id: string | null;
  source_book_id: string | null;
  checklist: { steps: Step[]; ready: boolean; note: string };
};
type Run = {
  id: string;
  company_id: string;
  period: string;
  status: 'draft' | 'ready' | 'blocked' | 'completed';
  steps: Preview;
};

export function FirmOnboarding({
  session,
  initialPeriod,
  onOpenBook,
}: {
  session: Session;
  initialPeriod?: string;
  onOpenBook: (target: { bookId: string; companyId: string; period: string }) => void;
}) {
  const [period, setPeriod] = useState(initialPeriod || new Date().toISOString().slice(0, 7));
  const [companies, setCompanies] = useState<Company[]>([]);
  const [target, setTarget] = useState('');
  const [source, setSource] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [newName, setNewName] = useState('');
  const [newTaxId, setNewTaxId] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void api<{ companies: Company[] }>('/admin/companies?limit=100', session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (controller.signal.aborted) return;
        setCompanies(value.companies);
        setTarget((current) => current || value.companies[0]?.id || '');
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [session, revision]);

  useEffect(() => {
    if (!target) {
      setPreview(null);
      return;
    }
    const controller = new AbortController();
    setBusy('preview');
    setError('');
    void api<Preview>(
      '/firm/onboarding/preview?' +
        new URLSearchParams({
          company_id: target,
          period,
          source_company_id: source && source !== target ? source : '',
        }),
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setPreview(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setPreview(null);
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy('');
      });
    return () => controller.abort();
  }, [session, target, source, period, revision]);

  async function createCompany() {
    if (!newName.trim()) return;
    setBusy('create');
    setError('');
    try {
      const value = await api<Company>('/companies', session, {
        method: 'POST',
        body: { name: newName.trim(), tax_id: newTaxId.trim() },
      });
      setNewName('');
      setNewTaxId('');
      setRevision((current) => current + 1);
      setTarget(value.id);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  async function startRun() {
    if (!target || !preview) return;
    setBusy('start');
    setError('');
    try {
      const value = await api<Run>('/firm/onboarding/runs', session, {
        method: 'POST',
        body: {
          company_id: target,
          period,
          source_company_id: source && source !== target ? source : '',
          reason: '客户标准化开通',
        },
      });
      setRun(value);
      setPreview(value.steps);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  async function refreshRun() {
    if (!run) return;
    setBusy('refresh');
    try {
      const value = await api<Run>(
        `/firm/onboarding/runs/${encodeURIComponent(run.id)}/refresh`,
        session,
        {
          method: 'POST',
        },
      );
      setRun(value);
      setPreview(value.steps);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  async function copyTemplates() {
    if (!source || !target || source === target) return;
    setBusy('copy');
    setError('');
    try {
      const value = await api<{ copied: number; skipped: number }>(
        '/firm/onboarding/copy-templates',
        session,
        {
          method: 'POST',
          body: {
            source_company_id: source,
            target_company_id: target,
            period,
            reason: '标准化开通复制同类客户模板',
          },
        },
      );
      setRevision((current) => current + 1);
      setError(`模板复制完成：新增 ${value.copied} 个，跳过 ${value.skipped} 个。`);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy('');
    }
  }

  const selected = companies.find((company) => company.id === target);
  const steps = preview?.checklist.steps ?? [];
  const required = steps.filter((step) => step.required);
  const completed = required.filter((step) => step.complete).length;
  return (
    <main className="firm-onboarding">
      <header className="onboarding-hero">
        <div>
          <p className="eyebrow">CLIENT ONBOARDING FACTORY</p>
          <h1>客户标准化开通</h1>
          <p>从企业资料到账套、权限、模板与首月流程，一次确认清楚。</p>
        </div>
        <label>
          开通月份
          <input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} />
        </label>
      </header>
      {error && (
        <p className={error.includes('复制完成') ? 'inline-success' : 'form-error'} role="status">
          {error}
        </p>
      )}
      <div className="onboarding-layout">
        <aside className="onboarding-controls">
          <section>
            <h2>选择目标客户</h2>
            <label>
              目标企业
              <select value={target} onChange={(event) => setTarget(event.target.value)}>
                {companies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              参考同类客户（可选）
              <select value={source} onChange={(event) => setSource(event.target.value)}>
                <option value="">不从其他客户复制</option>
                {companies
                  .filter((company) => company.id !== target)
                  .map((company) => (
                    <option key={company.id} value={company.id}>
                      {company.name}
                    </option>
                  ))}
              </select>
            </label>
            <button
              className="button secondary"
              type="button"
              disabled={!source || !target || busy !== ''}
              onClick={() => void copyTemplates()}
            >
              <Copy size={15} />
              复制记账模板
            </button>
          </section>
          <section>
            <h2>新增客户</h2>
            <label>
              企业名称
              <input value={newName} onChange={(event) => setNewName(event.target.value)} />
            </label>
            <label>
              税号（可稍后补充）
              <input value={newTaxId} onChange={(event) => setNewTaxId(event.target.value)} />
            </label>
            <button
              className="button secondary"
              type="button"
              disabled={!newName.trim() || busy !== ''}
              onClick={() => void createCompany()}
            >
              <Plus size={15} />
              建立客户档案
            </button>
          </section>
          <section>
            <h2>开通记录</h2>
            <button
              className="button primary"
              type="button"
              disabled={!preview || busy !== ''}
              onClick={() => void startRun()}
            >
              <ShieldCheck size={15} />
              创建可续跑开通记录
            </button>
            {run && (
              <button
                className="button secondary"
                type="button"
                disabled={busy !== ''}
                onClick={() => void refreshRun()}
              >
                <RefreshCw size={15} />
                重新检查开通条件
              </button>
            )}
            {run && (
              <p className="field-help">
                记录 {run.id.slice(0, 10)} · {run.status}
              </p>
            )}
          </section>
        </aside>
        <section className="onboarding-readiness">
          <header>
            <div>
              <p className="eyebrow">{selected?.name || '目标企业'}</p>
              <h2>
                {preview?.ready ? '基础条件已就绪' : `待完成 ${required.length - completed} 项检查`}
              </h2>
            </div>
            {busy === 'preview' && <Spinner />}
          </header>
          {preview?.blockers.length ? (
            <div className="onboarding-blockers" role="alert">
              <strong>必须处理后才能继续</strong>
              <ul>
                {preview.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <ol className="onboarding-factory-steps">
            {steps.map((step, index) => (
              <li key={step.id} className={step.complete ? 'complete' : ''}>
                <span>{step.complete ? '✓' : index + 1}</span>
                <div>
                  <strong>
                    {step.title}
                    {!step.required && <small>可选</small>}
                  </strong>
                  <p>{step.detail}</p>
                </div>
              </li>
            ))}
          </ol>
          <p className="field-help">{preview?.checklist.note}</p>
          {preview?.target_book_id && (
            <button
              className="button primary"
              type="button"
              onClick={() =>
                preview.target_book_id &&
                onOpenBook({
                  bookId: preview.target_book_id,
                  companyId: target,
                  period,
                })
              }
            >
              进入本月账套
              <ArrowRight size={15} />
            </button>
          )}
        </section>
      </div>
    </main>
  );
}
