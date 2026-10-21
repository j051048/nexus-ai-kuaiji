import { useEffect, useId, useReducer, useRef } from 'react';
import { ChevronDown, ClipboardList, LoaderCircle, RefreshCw } from 'lucide-react';
import { api, ApiError } from './api';
import {
  executionHandoffIntent,
  executionInitialState,
  executionPath,
  executionScopeKey,
  executionStateReducer,
  executionStatusLabel,
  executionTimestamp,
  executionValidationLabel,
  type ExecutionHandoffIntent,
  type ExecutionHandoffReceipt,
  type ExecutionKind,
  type ExecutionRecord,
} from './execution-types';
import type { Session } from './types';
import './execution-record.css';

interface Props {
  session: Session;
  companyId: string;
  kind: ExecutionKind;
  runId: string;
  contextKey?: string;
}

/** The identity key clears prior scope synchronously, before effects or a new read. */
export function ExecutionRecordPanel(props: Props) {
  const target = executionScopeKey(
    props.session,
    props.companyId,
    props.kind,
    props.runId,
    props.contextKey,
  );
  return <ExecutionRecordContent key={target} {...props} target={target} />;
}

function ExecutionRecordContent({ session, kind, runId, target }: Props & { target: string }) {
  const [state, dispatch] = useReducer(executionStateReducer, undefined, () =>
    executionInitialState(target, `${kind}:${runId}`),
  );
  const epoch = useRef(0);
  const request = useRef<AbortController | null>(null);
  const intent = useRef<ExecutionHandoffIntent | null>(null);
  const panelId = useId();
  const reasonId = useId();
  const errorId = useId();
  useEffect(() => {
    const revoke = (event: Event) => {
      const scope = (event as CustomEvent<string>).detail;
      if (scope && scope !== session.scope_key) return;
      request.current?.abort();
      request.current = null;
      intent.current = null;
      dispatch({ type: 'revoked', epoch: ++epoch.current });
    };
    window.addEventListener('accounting:unauthorized', revoke);
    window.addEventListener('accounting:forbidden', revoke);
    return () => {
      ++epoch.current;
      request.current?.abort();
      window.removeEventListener('accounting:unauthorized', revoke);
      window.removeEventListener('accounting:forbidden', revoke);
    };
  }, [session.scope_key]);

  const read = async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const version = ++epoch.current;
    dispatch({ type: 'begin', target, epoch: version, operation: 'read' });
    try {
      const record = await api<ExecutionRecord>(executionPath(kind, runId), session, {
        signal: controller.signal,
      });
      if (!controller.signal.aborted && version === epoch.current)
        dispatch({ type: 'loaded', target, epoch: version, record });
    } catch (failure) {
      if (!controller.signal.aborted && version === epoch.current) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          intent.current = null;
        dispatch({
          type: 'failed',
          target,
          epoch: version,
          status: failure instanceof ApiError ? failure.status : undefined,
        });
      }
    } finally {
      if (request.current === controller) request.current = null;
    }
  };
  const submit = async () => {
    if (request.current || state.caseId) return;
    const next = executionHandoffIntent(state.record, state.reason, intent.current, () =>
      crypto.randomUUID(),
    );
    if (!next) return;
    intent.current = next;
    const controller = new AbortController();
    request.current = controller;
    const version = ++epoch.current;
    dispatch({ type: 'begin', target, epoch: version, operation: 'submit' });
    try {
      const receipt = await api<ExecutionHandoffReceipt>(
        `${executionPath(kind, runId)}/handoff`,
        session,
        { method: 'POST', body: next.body, signal: controller.signal },
      );
      if (controller.signal.aborted || version !== epoch.current) return;
      if (
        receipt.execution_id !== `${kind}:${runId}` ||
        !/^[a-zA-Z0-9_-]{1,120}$/.test(receipt.case?.id ?? '')
      ) {
        dispatch({ type: 'failed', target, epoch: version, status: 409 });
        return;
      }
      dispatch({ type: 'completed', target, epoch: version, caseId: receipt.case.id });
    } catch (failure) {
      if (!controller.signal.aborted && version === epoch.current) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          intent.current = null;
        dispatch({
          type: 'failed',
          target,
          epoch: version,
          status: failure instanceof ApiError ? failure.status : undefined,
        });
      }
    } finally {
      if (request.current === controller) request.current = null;
    }
  };
  const record = state.record;
  const canCreate = !!record?.handoff.can_create && !!record.handoff.supported;
  const pending = state.phase !== 'idle';
  return (
    <section className="execution-record">
      <button
        type="button"
        className="execution-record-toggle"
        aria-expanded={state.open}
        aria-controls={panelId}
        onClick={() => {
          if (state.open) {
            ++epoch.current;
            request.current?.abort();
            request.current = null;
            dispatch({ type: 'close', epoch: epoch.current });
          } else void read();
        }}
      >
        <ClipboardList size={14} />
        {state.open ? '收起执行记录' : '查看执行记录'}
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {state.open && (
        <div id={panelId} className="execution-record-body">
          <div className="execution-record-heading">
            <strong>本次处理</strong>
            <button
              type="button"
              className="execution-record-refresh"
              disabled={pending}
              onClick={() => void read()}
            >
              <RefreshCw size={12} />
              重新查看
            </button>
          </div>
          {pending && (
            <p className="execution-record-status" role="status">
              <LoaderCircle size={14} />
              {state.phase === 'read' ? '读取当前执行记录…' : '正在登记人工核对任务…'}
            </p>
          )}
          {state.error && (
            <p className="execution-record-error" role="alert" id={errorId}>
              {state.error}
            </p>
          )}
          {record && (
            <>
              <dl className="execution-record-summary">
                <div>
                  <dt>处理状态</dt>
                  <dd>{executionStatusLabel(record.status)}</dd>
                </div>
                <div>
                  <dt>资料校验</dt>
                  <dd>{executionValidationLabel(record.validation.status)}</dd>
                </div>
              </dl>
              {record.failure && (
                <p className="execution-record-message">{record.failure.message}</p>
              )}
              {record.failure?.outcome_unknown && (
                <p className="execution-record-warning">
                  请求结果仍待核对，费用可能已经发生。请先联系会计或管理员核实。
                </p>
              )}
              {record.timeline.length > 0 && (
                <ol className="execution-record-timeline">
                  {record.timeline
                    .filter((item) => ['created', 'send_marked', 'finished'].includes(item.stage))
                    .map((item, index) => (
                      <li key={`${item.stage}:${index}`}>
                        <span>
                          {
                            (
                              {
                                created: '任务已创建',
                                send_marked: '已登记发送',
                                finished: '结束处理',
                              } as Record<string, string>
                            )[item.stage]
                          }
                        </span>
                        <time>{executionTimestamp(item.at)}</time>
                      </li>
                    ))}
                </ol>
              )}
              <p className="execution-record-boundary">凭证须由会计独立复核后导出。</p>
              {!canCreate && !state.caseId && (
                <p className="execution-record-message">
                  {record.handoff.message || '请联系有当前来源权限的会计核对处理。'}
                </p>
              )}
              {record.diagnostics && (
                <details className="execution-record-diagnostics">
                  <summary>技术摘要</summary>
                  <dl>
                    <div>
                      <dt>处理方式</dt>
                      <dd>
                        {record.diagnostics.deterministic_parse
                          ? '本地结构化解析'
                          : record.diagnostics.cache_hit
                            ? '复用已有识别结果'
                            : '常规处理'}
                      </dd>
                    </div>
                    <div>
                      <dt>发送记录</dt>
                      <dd>{record.diagnostics.send_marked ? '存在发送标记' : '未提供发送标记'}</dd>
                    </div>
                    <div>
                      <dt>费用记录</dt>
                      <dd>
                        {record.diagnostics.costs?.length
                          ? `${record.diagnostics.costs.length} 条（按费用台账核对）`
                          : '未提供费用记录'}
                      </dd>
                    </div>
                  </dl>
                </details>
              )}
            </>
          )}
          {state.caseId ? (
            <p className="execution-record-receipt" role="status">
              已创建人工核对任务<span>任务编号 {state.caseId}</span>
            </p>
          ) : (
            (canCreate || state.reason) && (
              <form
                className="execution-record-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submit();
                }}
              >
                <label htmlFor={reasonId}>
                  人工处理依据
                  <textarea
                    id={reasonId}
                    aria-describedby={state.error ? errorId : undefined}
                    value={state.reason}
                    rows={2}
                    maxLength={1000}
                    disabled={pending}
                    placeholder="说明需要核对的原件、资料或处理结果"
                    onChange={(event) => dispatch({ type: 'reason', value: event.target.value })}
                  />
                </label>
                <button
                  type="submit"
                  className="button secondary small"
                  disabled={
                    pending ||
                    !executionHandoffIntent(
                      record,
                      state.reason,
                      intent.current,
                      () => 'preview-only',
                    )
                  }
                >
                  创建人工核对任务
                </button>
              </form>
            )
          )}
        </div>
      )}
    </section>
  );
}
