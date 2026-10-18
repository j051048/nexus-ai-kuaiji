import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Copy, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
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
  resources?: {
    service_plan: boolean;
    material_templates: number;
    bank_accounts: number;
    opening_balance_ready: boolean;
    target_ledger_ready: boolean;
  };
};
type Run = {
  id: string;
  company_id: string;
  period: string;
  status: 'draft' | 'ready' | 'blocked' | 'completed';
  steps: Preview;
};

type OnboardingProps = {
  session: Session;
  initialPeriod?: string;
  onOpenBook: (target: { bookId: string; companyId: string; period: string }) => void;
};

export function FirmOnboarding(props: OnboardingProps) {
  return <OnboardingWorkspace key={props.session.scope_key} {...props} />;
}

function OnboardingWorkspace({ session, initialPeriod, onOpenBook }: OnboardingProps) {
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
  const [loading, setLoading] = useState(true);
  const active = useRef(true);
  const listEpoch = useRef(0);
  const previewEpoch = useRef(0);
  const mutationEpoch = useRef(0);
  const command = useRef(false);
  const selection = JSON.stringify([target, source, period]);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;

  useEffect(() => {
    active.current = true;
    const invalidate = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      mutationEpoch.current += 1;
      fail(new Error('当前授权可能已更新，已清除页面数据。请重新读取。'));
    };
    window.addEventListener('accounting:forbidden', invalidate);
    return () => {
      active.current = false;
      listEpoch.current += 1;
      previewEpoch.current += 1;
      mutationEpoch.current += 1;
      window.removeEventListener('accounting:forbidden', invalidate);
    };
  }, [session.scope_key]);

  function fail(failure: unknown) {
    // A permission failure must not leave another in-flight read able to restore old data.
    listEpoch.current += 1;
    previewEpoch.current += 1;
    setCompanies([]);
    setTarget('');
    setSource('');
    setPreview(null);
    setRun(null);
    setLoading(false);
    setBusy('');
    setError(errorMessage(failure));
  }

  useEffect(() => {
    const controller = new AbortController();
    const stamp = ++listEpoch.current;
    const current = () =>
      active.current && !controller.signal.aborted && stamp === listEpoch.current;
    setError('');
    setLoading(true);
    setBusy('');
    setCompanies([]);
    setPreview(null);
    setRun(null);
    void api<{ companies: Company[] }>('/companies', session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!current()) return;
        setCompanies(value.companies);
        setTarget((selected) =>
          value.companies.some((company) => company.id === selected)
            ? selected
            : value.companies[0]?.id || '',
        );
        setSource((selected) =>
          value.companies.some((company) => company.id === selected) ? selected : '',
        );
      })
      .catch((failure) => {
        if (current() || (active.current && failure instanceof ApiError && failure.status === 403))
          fail(failure);
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => controller.abort();
  }, [session, revision]);

  useEffect(() => {
    if (!target || !companies.some((company) => company.id === target)) {
      setPreview(null);
      setRun(null);
      return;
    }
    const controller = new AbortController();
    const stamp = ++previewEpoch.current;
    const current = () =>
      active.current &&
      !controller.signal.aborted &&
      stamp === previewEpoch.current &&
      selection === selectionRef.current;
    setBusy('preview');
    setError('');
    setPreview(null);
    setRun(null);
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
        if (current()) setPreview(value);
      })
      .catch((failure) => {
        if (current() || (active.current && failure instanceof ApiError && failure.status === 403))
          fail(failure);
      })
      .finally(() => {
        if (current()) setBusy('');
      });
    return () => controller.abort();
  }, [session, target, source, period, revision, companies, selection]);

  async function mutate(name: string, action: (current: () => boolean) => Promise<void>) {
    if (command.current || loading || busy) return;
    command.current = true;
    const stamp = ++mutationEpoch.current;
    const started = selection;
    const current = () =>
      active.current && stamp === mutationEpoch.current && started === selectionRef.current;
    setBusy(name);
    setError('');
    try {
      await action(current);
    } catch (failure) {
      if (current() || (active.current && failure instanceof ApiError && failure.status === 403))
        fail(failure);
    } finally {
      command.current = false;
      if (active.current && stamp === mutationEpoch.current) setBusy('');
    }
  }

  async function createCompany() {
    if (!newName.trim()) return;
    await mutate('create', async (current) => {
      const value = await api<Company>('/companies', session, {
        method: 'POST',
        body: { name: newName.trim(), tax_id: newTaxId.trim() },
      });
      if (!current()) return;
      setNewName('');
      setNewTaxId('');
      setRevision((current) => current + 1);
      setTarget(value.id);
    });
  }

  async function startRun() {
    if (!target || !preview) return;
    await mutate('start', async (current) => {
      const value = await api<Run>('/firm/onboarding/runs', session, {
        method: 'POST',
        body: {
          company_id: target,
          period,
          source_company_id: source && source !== target ? source : '',
          reason: '客户标准化开通',
        },
      });
      if (!current()) return;
      setRun(value);
      setPreview(value.steps);
    });
  }

  async function refreshRun() {
    if (!run) return;
    await mutate('refresh', async (current) => {
      const value = await api<Run>(
        `/firm/onboarding/runs/${encodeURIComponent(run.id)}/refresh`,
        session,
        {
          method: 'POST',
        },
      );
      if (!current()) return;
      setRun(value);
      setPreview(value.steps);
    });
  }

  async function copyTemplates() {
    if (!source || !target || source === target) return;
    await mutate('copy', async (current) => {
      const value = await api<{
        copied: number;
        skipped: number;
        service_plan: boolean;
        material_templates: number;
        material_templates_skipped: number;
        bank_accounts: number;
        bank_accounts_skipped: number;
      }>('/firm/onboarding/copy-templates', session, {
        method: 'POST',
        body: {
          source_company_id: source,
          target_company_id: target,
          period,
          reason: '标准化开通复制同类客户模板',
        },
      });
      if (!current()) return;
      setRevision((current) => current + 1);
      setError(
        `开通资源复制完成：记账模板新增 ${value.copied} 个、跳过 ${value.skipped} 个；` +
          `资料模板 ${value.material_templates} 个、银行账户 ${value.bank_accounts} 个；` +
          `服务单分工${value.service_plan ? '已复制' : '未复制'}。`,
      );
    });
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
          <input
            type="month"
            value={period}
            disabled={!!busy || loading}
            onChange={(event) => {
              setPreview(null);
              setRun(null);
              setPeriod(event.target.value);
            }}
          />
        </label>
      </header>
      {error && (
        <p
          className={error.includes('复制完成') ? 'inline-success' : 'form-error'}
          role={error.includes('复制完成') ? 'status' : 'alert'}
        >
          {error}
          {!error.includes('复制完成') && (
            <button
              className="text-button"
              type="button"
              disabled={!!busy || loading}
              onClick={() => setRevision((value) => value + 1)}
            >
              重新读取客户
            </button>
          )}
        </p>
      )}
      <div className="onboarding-layout">
        <aside className="onboarding-controls">
          <section>
            <h2>选择目标客户</h2>
            <label>
              目标企业
              <select
                value={target}
                disabled={!!busy || loading}
                onChange={(event) => {
                  setPreview(null);
                  setRun(null);
                  setTarget(event.target.value);
                }}
              >
                {companies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              参考同类客户（可选）
              <select
                value={source}
                disabled={!!busy || loading}
                onChange={(event) => {
                  setPreview(null);
                  setRun(null);
                  setSource(event.target.value);
                }}
              >
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
              disabled={!source || !target || busy !== '' || loading}
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
              <input
                value={newName}
                disabled={!!busy || loading}
                onChange={(event) => setNewName(event.target.value)}
              />
            </label>
            <label>
              税号（可稍后补充）
              <input
                value={newTaxId}
                disabled={!!busy || loading}
                onChange={(event) => setNewTaxId(event.target.value)}
              />
            </label>
            <button
              className="button secondary"
              type="button"
              disabled={!newName.trim() || busy !== '' || loading}
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
              disabled={!preview || busy !== '' || loading}
              onClick={() => void startRun()}
            >
              <ShieldCheck size={15} />
              创建可续跑开通记录
            </button>
            {run && (
              <button
                className="button secondary"
                type="button"
                disabled={busy !== '' || loading}
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
          {preview?.resources && (
            <div className="onboarding-resources" aria-label="可复制开通资源">
              <span>服务单分工 {preview.resources.service_plan ? '可复制' : '已有/无来源'}</span>
              <span>资料模板 {preview.resources.material_templates} 个</span>
              <span>银行账户 {preview.resources.bank_accounts} 个</span>
              <span>
                期初余额 {preview.resources.opening_balance_ready ? '已确认' : '待导入确认'}
              </span>
            </div>
          )}
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
              disabled={!!busy || loading}
              onClick={() =>
                preview.target_book_id &&
                onOpenBook({
                  bookId: preview.target_book_id,
                  companyId: preview.company_id,
                  period: preview.period,
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
