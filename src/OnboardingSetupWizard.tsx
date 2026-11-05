import { lazy, Suspense, useRef, useState } from 'react';
import { useScopedQuery } from './useScopedQuery';
import { api } from './api';
import type { Book } from './types';
import { emptyWorkspaceRoute, workspaceRouteUrl } from './workspace-route';
const OpeningPreparationPanel = lazy(() =>
  import('./OpeningPreparationPanel').then((m) => ({ default: m.OpeningPreparationPanel })),
);
const SubledgerSetupPanel = lazy(() =>
  import('./SubledgerSetupPanel').then((m) => ({ default: m.SubledgerSetupPanel })),
);
const AdminMembers = lazy(() =>
  import('./AdminMembers').then((m) => ({ default: m.AdminMembers })),
);
const ManualVoucherReviewPanel = lazy(() =>
  import('./ManualVoucherReviewPanel').then((m) => ({ default: m.ManualVoucherReviewPanel })),
);
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

const setupKinds: Record<string, string> = {
  template: 'opening',
  accounts: 'opening',
  opening: 'opening',
  control: 'subledger',
  bank: 'bank',
  staffing: 'staffing',
  first: 'trial',
};
export function SetupStepList({
  body,
  onOpen,
}: {
  body: SetupBody;
  onOpen?: (step: SetupStep) => void;
}) {
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
          <li id={`setup-${step.id}`} key={step.id} data-state={step.complete ? 'done' : 'todo'}>
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
                {onOpen &&
                setupKinds[step.id] === step.action_target.kind &&
                step.action_target.book_id === body.book_id &&
                step.action_target.period === body.period ? (
                  <button type="button" className="button secondary" onClick={() => onOpen(step)}>
                    办理：{step.title}
                  </button>
                ) : !onOpen && IN_PAGE_KINDS.has(step.action_target.kind) ? (
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

export function OnboardingSetupWizard(props: Props) {
  return (
    <SetupContent
      key={`${props.session.scope_key}:${props.companyId}:${props.bookId}:${props.period}`}
      {...props}
    />
  );
}
function SetupContent({ session, companyId, bookId, period }: Props) {
  const [selected, setSelected] = useState<SetupStep | null>(null);
  const [busy, setBusy] = useState(false);
  const returnStep = useRef('');
  const query = useScopedQuery(async (signal) => {
    const params = new URLSearchParams({ company_id: companyId, book_id: bookId, period });
    const next = await api<SetupBody>(`/firm/onboarding/setup?${params}`, session, { signal });
    if (
      next.company_id !== companyId ||
      next.book_id !== bookId ||
      next.period !== period ||
      !Array.isArray(next.steps) ||
      next.steps.some(
        (step) =>
          typeof step.complete !== 'boolean' ||
          !step.action_target ||
          step.action_target.book_id !== bookId ||
          step.action_target.period !== period,
      )
    )
      throw new Error('开账进度范围无法核验，请重新读取。');
    return next;
  }, `${session.scope_key}:${companyId}:${bookId}:${period}`);
  const bookQuery = useScopedQuery(async (signal) => {
    if (selected?.id !== 'staffing') return null;
    const next = await api<{ book: Book }>(`/books/${encodeURIComponent(bookId)}`, session, {
      signal,
    });
    if (next.book.id !== bookId || next.book.company_id !== companyId)
      throw new Error('账套范围无法核验。');
    return next.book;
  }, `${session.scope_key}:${bookId}:${selected?.id}`);
  function back() {
    setSelected(null);
    query.reload();
  }
  if (selected)
    return (
      <section aria-label="开账步骤办理">
        <p>
          正在办理：{selected.title} · {period}
        </p>
        <button className="button secondary" disabled={busy} onClick={back}>
          返回开账清单并重查
        </button>
        <Suspense fallback={<p role="status">正在打开办理表单…</p>}>
          {['template', 'accounts', 'opening'].includes(selected.id) && (
            <OpeningPreparationPanel
              key={selected.id}
              session={session}
              companyId={companyId}
              bookId={bookId}
              period={period}
              step={selected.id}
              onBusyChange={setBusy}
            />
          )}
          {['control', 'bank'].includes(selected.id) && (
            <SubledgerSetupPanel
              session={session}
              bookId={bookId}
              period={period}
              panel={selected.id === 'control' ? 'subledger' : 'bank'}
              onClose={back}
              onChanged={query.reload}
              onBusyChange={setBusy}
            />
          )}
          {selected.id === 'staffing' &&
            (bookQuery.error ? (
              <p role="alert">{bookQuery.error}</p>
            ) : (
              bookQuery.data && (
                <AdminMembers
                  session={session}
                  kind="book"
                  books={[bookQuery.data]}
                  onBusy={setBusy}
                  onChanged={async () => query.reload()}
                />
              )
            ))}
          {selected.id === 'first' && (
            <>
              <a
                className="button secondary"
                href={workspaceRouteUrl({
                  ...emptyWorkspaceRoute,
                  view: 'business',
                  companyId,
                  period,
                })}
              >
                打开本企业业务事项（返回后重查清单）
              </a>
              <ManualVoucherReviewPanel session={session} bookId={bookId} companyId={companyId} />
            </>
          )}
        </Suspense>
      </section>
    );
  return (
    <section className="setup-wizard" aria-label="开账准备向导">
      {query.loading && <p role="status">正在读取开账准备进度……</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {query.data && (
        <>
          <p role="status">
            {returnStep.current ? `已返回${returnStep.current}，以下为重新读取的进度。` : ''}
          </p>
          <SetupStepList
            body={query.data}
            onOpen={(step) => {
              returnStep.current = step.title;
              setSelected(step);
            }}
          />
        </>
      )}
      <button
        type="button"
        className="button secondary"
        disabled={query.loading}
        onClick={query.reload}
      >
        重新检查进度
      </button>
    </section>
  );
}
