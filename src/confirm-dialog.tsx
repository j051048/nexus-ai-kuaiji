import { useEffect, useState } from 'react';
import { Modal } from './AppPrimitives';
import './confirm-dialog.css';

/** A confirmation the shell asks before a consequential action. */
export interface ConfirmRequest {
  title: string;
  description: string;
  /** The object the action touches (voucher, close run, package…). */
  subject?: string;
  /** The legal entity the action happens in. */
  company?: string;
  /** The accounting period the action happens in. */
  period?: string;
  /** What the user loses or blocks by confirming. */
  consequence?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /**
   * Irreversible actions (lock, post, publish, send) must be confirmed by
   * typing the period or the object name; a plain "OK" is too easy to hit.
   */
  irreversible?: boolean;
  /** Text the user must type. Defaults to period, then subject. */
  phrase?: string;
}

interface Pending {
  request: ConfirmRequest;
  resolve: (value: boolean) => void;
}

let pending: Pending | null = null;
const listeners = new Set<(next: Pending | null) => void>();

function publish(next: Pending | null) {
  pending = next;
  for (const listener of listeners) listener(next);
}

/**
 * Ask the mounted `ConfirmHost` for an answer. Resolves false when no host is
 * mounted (the action is refused rather than silently proceeding).
 */
export function requestConfirm(request: ConfirmRequest): Promise<boolean> {
  if (!listeners.size) return Promise.resolve(false);
  return new Promise<boolean>((resolve) => {
    publish({ request, resolve });
  });
}

function settle(value: boolean) {
  const current = pending;
  publish(null);
  current?.resolve(value);
}

export function requiredPhrase(request: ConfirmRequest): string {
  return (request.phrase ?? request.period ?? request.subject ?? '').trim();
}

export function canConfirm(request: ConfirmRequest, typed: string): boolean {
  if (!request.irreversible) return true;
  const expected = requiredPhrase(request);
  // An irreversible action with nothing identifiable to type must not proceed:
  // otherwise "type to confirm" degrades into a plain OK button.
  if (!expected) return false;
  return typed.trim() === expected;
}

function Row({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div className="confirm-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function ConfirmDialog({
  request,
  typed,
  onTyped,
  onConfirm,
  onCancel,
  busy,
}: {
  request: ConfirmRequest;
  typed: string;
  onTyped: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const expected = requiredPhrase(request);
  const ready = canConfirm(request, typed) && !busy;
  return (
    <div className="confirm-dialog" data-irreversible={request.irreversible ? 'true' : 'false'}>
      <p className="confirm-description">{request.description}</p>
      <dl className="confirm-facts">
        <Row label="对象" value={request.subject} />
        <Row label="单位" value={request.company} />
        <Row label="期间" value={request.period} />
      </dl>
      {request.consequence && (
        <p className="confirm-consequence" role="note">
          {request.consequence}
        </p>
      )}
      {request.irreversible && (
        <label className="confirm-phrase">
          <span>
            {expected
              ? `请输入「${expected}」以确认`
              : '该操作缺少可核对的对象或期间，系统不会放行'}
          </span>
          <input
            value={typed}
            onChange={(event) => onTyped(event.target.value)}
            autoComplete="off"
            disabled={busy}
          />
        </label>
      )}
      <div className="confirm-actions">
        <button type="button" className="button secondary" onClick={onCancel} autoFocus>
          {request.cancelLabel ?? '取消'}
        </button>
        <button type="button" className="button danger" onClick={onConfirm} disabled={!ready}>
          {request.confirmLabel ?? '确认'}
        </button>
      </div>
    </div>
  );
}

/**
 * Mount once near the app root. Every `requestConfirm` call renders here, so
 * call sites never need to insert dialog markup into their own trees.
 */
export function ConfirmHost() {
  const [current, setCurrent] = useState<Pending | null>(pending);
  const [typed, setTyped] = useState('');

  useEffect(() => {
    const listener = (next: Pending | null) => {
      setTyped('');
      setCurrent(next);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  if (!current) return null;
  return (
    <Modal title={current.request.title} onClose={() => settle(false)}>
      <ConfirmDialog
        request={current.request}
        typed={typed}
        onTyped={setTyped}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </Modal>
  );
}
