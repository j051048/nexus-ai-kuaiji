import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import './formal-close.css';

export interface SetupActionTarget {
  kind: string;
  book_id: string;
  period: string;
}

export interface SetupStep {
  id: string;
  title: string;
  complete: boolean;
  required: boolean;
  detail: string;
  owner_role: string;
  next_action: string;
  action_target: SetupActionTarget;
}

export interface SetupBody {
  company_id: string;
  book_id: string;
  period: string;
  ready: boolean;
  steps: SetupStep[];
  updated_at: string;
  note: string;
}

interface Props {
  session: Session;
  companyId: string;
  bookId: string;
  period: string;
}

/** Kinds whose办理入口 sits inside the month-close panel rendered on this page. */
const IN_PAGE_KINDS = new Set(['subledger', 'bank']);

export function SetupStepList({ body }: { body: SetupBody }) {
  const done = body.steps.filter((step) => step.complete).length;
  return (
    <div className="setup-wizard-body" aria-label="开账准备向导">
      <p role="status" className="setup-wizard-progress">
        {body.ready
          ? `开账准备 ${done}/${body.steps.length} 项已完成，可以开始本月记账。`
          : `开账准备 ${done}/${body.steps.length} 项已完成；未完成项按岗位列在下方。`}
      </p>
      <ol className="setup-wizard-steps">
        {body.steps.map((step, index) => (
          <li key={step.id} data-state={step.complete ? 'done' : 'todo'}>
            <div className="setup-wizard-head">
              <span className="setup-wizard-index">{index + 1}</span>
              <strong>{step.title}</strong>
              <span className={`setup-wizard-badge${step.complete ? ' done' : ''}`}>
                {step.complete ? '已完成' : '待办理'}
              </span>
            </div>
            <p>{step.detail}</p>
            {!step.complete && (
              <>
                <p className="formal-close-guidance">
                  负责岗位：{step.owner_role || '待指派'} · 下一步：{step.next_action}
                </p>
                {IN_PAGE_KINDS.has(step.action_target.kind) ? (
                  <a className="button secondary" href="#formal-close">
                    去月结面板办理
                  </a>
                ) : (
                  <p className="setup-wizard-hint">
                    在对应面板办理；本向导只读取已登记记录，不会代替确认。
                  </p>
                )}
              </>
            )}
          </li>
        ))}
      </ol>
      <p className="setup-wizard-note">{body.note}</p>
    </div>
  );
}

export function OnboardingSetupWizard({ session, companyId, bookId, period }: Props) {
  const [body, setBody] = useState<SetupBody | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const live = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const query = `company_id=${encodeURIComponent(companyId)}&book_id=${encodeURIComponent(
        bookId,
      )}&period=${encodeURIComponent(period)}`;
      const next = await api<SetupBody>(`/firm/onboarding/setup?${query}`, session);
      if (live.current) setBody(next);
    } catch (failure) {
      if (live.current) setError(errorMessage(failure));
    } finally {
      if (live.current) setLoading(false);
    }
  }, [bookId, companyId, period, session]);

  useEffect(() => {
    live.current = true;
    void load();
    return () => {
      live.current = false;
    };
  }, [load]);

  if (loading) {
    return (
      <section className="setup-wizard" aria-label="开账准备向导">
        <p role="status">正在读取开账准备进度……</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="setup-wizard" aria-label="开账准备向导">
        <p role="alert">{error}</p>
        <button type="button" className="button secondary" onClick={() => void load()}>
          重新读取进度
        </button>
      </section>
    );
  }
  if (!body) return null;
  return (
    <section className="setup-wizard" aria-label="开账准备向导">
      <SetupStepList body={body} />
      <button type="button" className="button secondary" onClick={() => void load()}>
        重新检查进度
      </button>
    </section>
  );
}
