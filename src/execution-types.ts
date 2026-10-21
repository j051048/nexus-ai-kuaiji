import type { Session } from './types';

export type ExecutionKind = 'chat' | 'ocr' | 'journal' | 'intake';
export interface ExecutionRecord {
  schema: 'ak04-execution-v1';
  execution_id: string;
  kind: ExecutionKind;
  status: string;
  accounting_approved: false;
  validation: { code: string; status: string; finding_codes: string[] };
  failure: {
    category: string;
    code: string;
    message: string;
    outcome_unknown: boolean;
    automatic_retry: boolean | null;
  } | null;
  handoff: {
    required: boolean;
    reason_code: string;
    action: string;
    automatic_retry: boolean | null;
    supported?: boolean;
    can_create?: boolean;
    fingerprint?: string | null;
    message?: string;
  };
  timeline: { stage: string; at: string; source: string }[];
  diagnostics?: {
    send_marked?: boolean;
    cache_hit?: boolean;
    deterministic_parse?: boolean;
    costs?: {
      state?: string;
      input_tokens?: number | null;
      output_tokens?: number | null;
      estimated_micro?: number | null;
      confirmed_micro?: number | null;
    }[];
    attempts?: unknown[];
  };
}
export interface ExecutionHandoffReceipt {
  execution_id: string;
  case: { id: string };
  automatic_retry: false;
}
export interface ExecutionHandoffIntent {
  signature: string;
  body: { expected_fingerprint: string; reason: string; idempotency_key: string };
}
export function executionScopeKey(
  session: Session,
  companyId: string,
  kind: ExecutionKind,
  runId: string,
  contextKey = '',
): string {
  return JSON.stringify([
    session.scope_key,
    session.user_id,
    session.firm_id,
    session.role,
    companyId,
    kind,
    runId,
    contextKey,
  ]);
}
export function executionPath(kind: ExecutionKind, runId: string): string {
  return `/executions/${kind}/${encodeURIComponent(runId)}`;
}
export function executionHandoffIntent(
  record: ExecutionRecord | null,
  reason: string,
  previous: ExecutionHandoffIntent | null,
  createKey: () => string,
): ExecutionHandoffIntent | null {
  const fingerprint = record?.handoff.fingerprint;
  const trimmed = reason.trim();
  if (
    !record?.handoff.can_create ||
    !record.handoff.supported ||
    !fingerprint ||
    !/^[0-9a-f]{64}$/.test(fingerprint) ||
    !trimmed ||
    trimmed.length > 1000 ||
    trimmed.includes('\0')
  )
    return null;
  const signature = JSON.stringify([record.execution_id, fingerprint, trimmed]);
  if (previous?.signature === signature) return previous;
  return {
    signature,
    body: { expected_fingerprint: fingerprint, reason: trimmed, idempotency_key: createKey() },
  };
}
export interface ExecutionPanelState {
  target: string;
  executionId: string;
  open: boolean;
  epoch: number;
  phase: 'idle' | 'read' | 'submit';
  record: ExecutionRecord | null;
  reason: string;
  caseId: string | null;
  error: string;
}
export type ExecutionPanelAction =
  | { type: 'reason'; value: string }
  | { type: 'close'; epoch: number }
  | { type: 'revoked'; epoch: number }
  | { type: 'begin'; target: string; epoch: number; operation: 'read' | 'submit' }
  | { type: 'loaded'; target: string; epoch: number; record: ExecutionRecord }
  | { type: 'completed'; target: string; epoch: number; caseId: string }
  | { type: 'failed'; target: string; epoch: number; status?: number };
export function executionInitialState(target: string, executionId: string): ExecutionPanelState {
  return {
    target,
    executionId,
    open: false,
    epoch: 0,
    phase: 'idle',
    record: null,
    reason: '',
    caseId: null,
    error: '',
  };
}
export function executionFailureMessage(status?: number): string {
  if (status === 401 || status === 403 || status === 404)
    return '当前授权已变化，已清除执行记录。请重新打开当前资料。';
  if (status === 409) return '执行或资料已变化，请重新查看执行记录后再确认。填写的依据已保留。';
  return '暂未确认操作结果，请重新查看执行记录后再确认。填写的依据已保留。';
}
export function executionStateReducer(
  state: ExecutionPanelState,
  action: ExecutionPanelAction,
): ExecutionPanelState {
  if (action.type === 'reason') return { ...state, reason: action.value };
  if (action.type === 'close')
    return { ...state, open: false, epoch: action.epoch, phase: 'idle', record: null, error: '' };
  if (action.type === 'revoked')
    return {
      ...executionInitialState(state.target, state.executionId),
      open: true,
      epoch: action.epoch,
      error: executionFailureMessage(403),
    };
  if (action.target !== state.target) return state;
  if (action.type === 'begin')
    return {
      ...state,
      open: true,
      epoch: action.epoch,
      phase: action.operation,
      record: action.operation === 'read' ? null : state.record,
      caseId: action.operation === 'read' ? null : state.caseId,
      error: '',
    };
  if (action.epoch !== state.epoch) return state;
  if (action.type === 'loaded') {
    if (
      action.record.schema !== 'ak04-execution-v1' ||
      action.record.execution_id !== state.executionId
    )
      return { ...state, phase: 'idle', record: null, error: executionFailureMessage(409) };
    return { ...state, record: action.record, phase: 'idle', error: '' };
  }
  if (action.type === 'completed')
    return { ...state, phase: 'idle', caseId: action.caseId, error: '' };
  return {
    ...state,
    phase: 'idle',
    record: null,
    error: executionFailureMessage(action.status),
    reason:
      action.status === 401 || action.status === 403 || action.status === 404 ? '' : state.reason,
    caseId:
      action.status === 401 || action.status === 403 || action.status === 404 ? null : state.caseId,
  };
}
export function executionStatusLabel(status: string): string {
  return (
    (
      {
        queued: '等待处理',
        running: '正在处理',
        succeeded: '处理已完成',
        failed: '处理未完成',
        stale: '资料已变化',
        cancelled: '已取消',
        interrupted: '结果待核对',
        needs_attention: '需人工核对',
      } as Record<string, string>
    )[status] ?? '处理记录'
  );
}
export function executionValidationLabel(status: string): string {
  return (
    (
      {
        checked: '程序校验已完成',
        blocked: '存在待处理问题',
        requires_review: '等待人工核对',
        not_checked: '尚无完整校验记录',
      } as Record<string, string>
    )[status] ?? '等待人工核对'
  );
}
export function executionTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '未记录时间';
}
