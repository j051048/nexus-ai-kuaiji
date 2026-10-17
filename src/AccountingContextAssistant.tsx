import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  BookOpen,
  CheckCircle2,
  FileSearch,
  FileText,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import {
  contextActions,
  contextQuestion,
  matchesAccountingScope,
  type AccountingContext,
  type AccountingContextAction,
  type AccountingContextAnswer,
  type AccountingContextEvidence,
} from './accounting-context';
import type { Session } from './types';
import './context-assistant.css';

export type { AccountingContextAction, AccountingContextEvidence } from './accounting-context';
interface Turn {
  key: string;
  question: string;
  response: AccountingContextAnswer;
}
export interface AccountingContextAssistantProps {
  session: Session;
  bookId: string;
  draftId: string;
  draftVersion?: number;
  hasUnsavedChanges?: boolean;
  actionTypes?: AccountingContextAction['type'][];
  onClose?: () => void;
  onOpenEvidence?: (evidenceId: string) => void;
  onAction?: (action: AccountingContextAction) => void;
  embedded?: boolean;
}

/** The assistant summarizes persisted, authorized evidence. It never submits a journal. */
export function AccountingContextAssistant({
  session,
  bookId,
  draftId,
  draftVersion,
  hasUnsavedChanges = false,
  actionTypes,
  onClose,
  onOpenEvidence,
  onAction,
  embedded = false,
}: AccountingContextAssistantProps) {
  const scopeKey = `${session.scope_key}:${session.user_id}:${bookId}:${draftId}:${draftVersion ?? 'current'}`;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const [loaded, setLoaded] = useState<{ key: string; context: AccountingContext } | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]),
    [question, setQuestion] = useState('');
  const [questionScope, setQuestionScope] = useState(scopeKey);
  const [loading, setLoading] = useState(true),
    [answering, setAnswering] = useState(false),
    [error, setError] = useState('');
  const [permissionRevoked, setPermissionRevoked] = useState(false);
  const epoch = useRef(0),
    questionBusy = useRef(false),
    controller = useRef<AbortController | null>(null),
    end = useRef<HTMLDivElement>(null);
  const context = loaded?.key === scopeKey ? loaded.context : null;
  const visibleTurns = turns.filter((turn) => turn.key === scopeKey);
  const visibleQuestion = questionScope === scopeKey ? question : '';
  const base = `/agent-workspace/context/books/${encodeURIComponent(bookId)}/drafts/${encodeURIComponent(draftId)}`;

  const clearSensitive = useCallback(() => {
    epoch.current++;
    controller.current?.abort();
    questionBusy.current = false;
    setLoaded(null);
    setTurns([]);
    setQuestion('');
    setLoading(false);
    setAnswering(false);
    setPermissionRevoked(true);
  }, []);
  const load = useCallback(async () => {
    controller.current?.abort();
    const signalController = new AbortController();
    controller.current = signalController;
    const generation = ++epoch.current,
      requestedScope = scopeKey;
    questionBusy.current = false;
    setLoaded(null);
    setTurns([]);
    setQuestion('');
    setQuestionScope(scopeKey);
    setAnswering(false);
    setLoading(true);
    setError('');
    setPermissionRevoked(false);
    try {
      const value = await api<AccountingContext>(base, session, {
        signal: signalController.signal,
      });
      if (
        signalController.signal.aborted ||
        generation !== epoch.current ||
        requestedScope !== currentScope.current
      )
        return;
      if (
        value.mode !== 'evidence_summary' ||
        !matchesAccountingScope(value.scope, bookId, draftId, draftVersion)
      )
        throw new Error('凭证版本已变化，请重新读取当前凭证，再打开助手。');
      setLoaded({ key: requestedScope, context: value });
    } catch (failure) {
      if (
        signalController.signal.aborted ||
        generation !== epoch.current ||
        requestedScope !== currentScope.current
      )
        return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) clearSensitive();
      setError(errorMessage(failure));
    } finally {
      if (
        !signalController.signal.aborted &&
        generation === epoch.current &&
        requestedScope === currentScope.current
      )
        setLoading(false);
    }
  }, [base, session, scopeKey, bookId, draftId, draftVersion, clearSensitive]);
  useEffect(() => {
    void load();
    return () => {
      epoch.current++;
      controller.current?.abort();
      questionBusy.current = false;
    };
  }, [load]);
  useEffect(() => {
    const revoke = () => {
      clearSensitive();
      setError('当前授权已更新，资料与问答已清空。请返回工作区重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [clearSensitive]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
  }, [turns.length, answering]);

  async function ask(value: string) {
    const submitted = contextQuestion(value);
    if (!submitted || !context || questionBusy.current || permissionRevoked) return;
    const requestedScope = scopeKey,
      generation = epoch.current,
      basis = context.scope;
    questionBusy.current = true;
    setAnswering(true);
    setError('');
    setQuestion('');
    const signalController = new AbortController();
    controller.current?.abort();
    controller.current = signalController;
    try {
      const response = await api<AccountingContextAnswer>(`${base}/explain`, session, {
        method: 'POST',
        body: { question: submitted },
        signal: signalController.signal,
      });
      if (
        signalController.signal.aborted ||
        generation !== epoch.current ||
        requestedScope !== currentScope.current
      )
        return;
      if (
        response.mode !== 'evidence_summary' ||
        !matchesAccountingScope(response.scope, bookId, draftId, draftVersion) ||
        response.scope.draft_version !== basis.draft_version ||
        response.scope.fact_version !== basis.fact_version
      ) {
        setLoaded(null);
        setTurns([]);
        throw new Error('业务事实或凭证已更新，请重新读取资料后再核对。');
      }
      setTurns((previous) =>
        [
          ...previous.filter((turn) => turn.key === requestedScope),
          { key: requestedScope, question: submitted, response },
        ].slice(-6),
      );
    } catch (failure) {
      if (
        signalController.signal.aborted ||
        generation !== epoch.current ||
        requestedScope !== currentScope.current
      )
        return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) clearSensitive();
      setError(errorMessage(failure));
    } finally {
      if (
        !signalController.signal.aborted &&
        generation === epoch.current &&
        requestedScope === currentScope.current
      ) {
        questionBusy.current = false;
        setAnswering(false);
      }
    }
  }
  function citation(evidence: Pick<AccountingContextEvidence, 'id' | 'label' | 'detail'>) {
    const known = context?.evidence.some((row) => row.id === evidence.id);
    return (
      <button
        key={evidence.id}
        className="context-citation"
        disabled={!known || !onOpenEvidence}
        onClick={() => onOpenEvidence?.(evidence.id)}
        title={evidence.detail}
      >
        <FileText size={14} />
        <span>{evidence.label}</span>
      </button>
    );
  }
  const actions = context
    ? contextActions(context.actions, context.scope).filter(
        (action) => !actionTypes || actionTypes.includes(action.type),
      )
    : [];
  return (
    <aside
      className={'accounting-context-assistant' + (embedded ? ' embedded' : '')}
      aria-label="当前凭证会计助手"
      data-scope-book={bookId}
      data-scope-draft={draftId}
    >
      {!embedded && (
        <header className="context-assistant-header">
          <div className="context-assistant-mark">
            <Sparkles size={18} />
          </div>
          <div>
            <p>衡账 · 会计助手</p>
            <h3>依据当前资料</h3>
          </div>
          {onClose && (
            <button
              className="icon-button context-assistant-close"
              aria-label="关闭会计助手"
              onClick={onClose}
            >
              <X size={18} />
            </button>
          )}
        </header>
      )}
      <div className="context-assistant-scroll">
        {loading ? (
          <div className="context-assistant-loading" role="status">
            <LoaderCircle size={20} className="spinner" />
            <p>正在读取当前凭证与已授权证据…</p>
          </div>
        ) : context ? (
          <>
            <div className="context-scope-card">
              <span>
                <BookOpen size={13} />
                {context.scope.company_name}
              </span>
              <strong>
                {context.scope.period} · 凭证 v{context.scope.draft_version}
              </strong>
              <p>
                {context.scope.book_name}
                {context.scope.fact_version !== null &&
                  ` · 业务事实 v${context.scope.fact_version}`}
              </p>
            </div>
            {hasUnsavedChanges && (
              <p className="context-unsaved-note">
                <TriangleAlert size={14} />
                当前分录有未保存修改。以下依据已保存的 v{context.scope.draft_version}
                ，保存后会重新读取。
              </p>
            )}
            {context.draft.issues.length > 0 ? (
              <section className="context-blocker-card">
                <h4>
                  <TriangleAlert size={15} />
                  需人工处理 · {context.draft.issues.length} 项
                </h4>
                <ul>
                  {context.draft.issues.map((issue, index) => (
                    <li key={index}>{issue}</li>
                  ))}
                </ul>
              </section>
            ) : (
              <div className="context-clean-state">
                <CheckCircle2 size={15} />
                当前校验未发现阻断，仍需核对原件与会计处理。
              </div>
            )}
            <section className="context-basis-card">
              <h4>这笔建议从哪里来</h4>
              <p>{context.draft.explanation || '当前资料未记录完整的建议说明，请结合原件核对。'}</p>
              {context.evidence.length > 0 && (
                <div className="context-citations">{context.evidence.map(citation)}</div>
              )}
              {!context.evidence.length && (
                <p className="context-muted">尚无可引用的原件信息，需要补充资料。</p>
              )}
            </section>
            {context.timeline.length > 0 && (
              <details className="context-evidence-trail">
                <summary>
                  资料与处理轨迹 <span>{context.timeline.length}</span>
                </summary>
                <ol>
                  {context.timeline.map((item) => (
                    <li key={item.id}>
                      <strong>{item.label}</strong>
                      {item.detail && <p>{item.detail}</p>}
                      {item.at && (
                        <time dateTime={item.at}>{item.at.replace('T', ' ').slice(0, 19)}</time>
                      )}
                    </li>
                  ))}
                </ol>
              </details>
            )}
            {visibleTurns.length === 0 && context.suggestions.length > 0 && (
              <section className="context-suggestion-list" aria-label="建议询问">
                <h4>从这里开始</h4>
                {context.suggestions.slice(0, 4).map((value) => (
                  <button key={value} disabled={answering} onClick={() => void ask(value)}>
                    <MessageSquareText size={14} />
                    {value}
                  </button>
                ))}
              </section>
            )}
            {visibleTurns.map((turn, index) => (
              <article className="context-answer-turn" key={`${index}:${turn.question}`}>
                <div className="context-user-question">
                  <MessageSquareText size={14} />
                  <p>{turn.question}</p>
                </div>
                <div className="context-answer-card">
                  <h4>
                    <FileSearch size={15} />
                    资料归纳
                  </h4>
                  <p>{turn.response.answer}</p>
                  {turn.response.citations.length > 0 && (
                    <div className="context-citations">{turn.response.citations.map(citation)}</div>
                  )}
                </div>
              </article>
            ))}
            {answering && (
              <p className="context-answering" role="status">
                <LoaderCircle size={14} className="spinner" />
                正在核对当前资料…
              </p>
            )}
            {actions.length > 0 && (
              <div className="context-next-actions" aria-label="可执行的人工操作">
                {actions.map((action) => (
                  <button
                    key={action.type}
                    className="text-button"
                    disabled={!action.enabled || !onAction || answering}
                    title={action.reason}
                    onClick={() => onAction?.(action)}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            )}
          </>
        ) : null}
        {error && (
          <div className="context-error" role="alert">
            <TriangleAlert size={15} />
            <p>{error}</p>
            {!permissionRevoked && (
              <button className="text-button" disabled={answering} onClick={() => void load()}>
                <RefreshCw size={13} />
                重新读取
              </button>
            )}
          </div>
        )}
        <div ref={end} />
      </div>
      <footer className="context-assistant-footer">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void ask(visibleQuestion);
          }}
        >
          <label className="context-visually-hidden" htmlFor={`context-question-${draftId}`}>
            询问当前凭证
          </label>
          <textarea
            id={`context-question-${draftId}`}
            rows={2}
            maxLength={1000}
            value={visibleQuestion}
            placeholder="询问这笔分录、金额或缺失证据…"
            disabled={!context || answering || permissionRevoked}
            onChange={(event) => {
              setQuestionScope(scopeKey);
              setQuestion(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void ask(visibleQuestion);
              }
            }}
          />
          <button
            aria-label="发送当前凭证问题"
            disabled={
              !context || answering || !contextQuestion(visibleQuestion) || permissionRevoked
            }
          >
            {answering ? <LoaderCircle size={16} className="spinner" /> : <ArrowUp size={17} />}
          </button>
        </form>
        <p>
          <ShieldCheck size={13} />
          <span>仅整理已存资料。审核、修改与导出由会计确认。</span>
        </p>
        {context?.notice && <p className="context-service-notice">{context.notice}</p>}
      </footer>
    </aside>
  );
}

export default AccountingContextAssistant;
