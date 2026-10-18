import { Suspense, useEffect, useState } from 'react';
import { AdminMfaGate } from './workspace-modules';
import { Modal, Spinner } from './AppPrimitives';
import type { Session } from './types';

/** Step-up is separate from login and never replays the rejected write. */
export function WorkspaceMfaPrompt({
  session,
  onVerified,
  onLogout,
}: {
  session: Session;
  onVerified: () => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  const [needed, setNeeded] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const handle = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      setNeeded(true);
    };
    window.addEventListener('accounting:mfa-required', handle);
    return () => window.removeEventListener('accounting:mfa-required', handle);
  }, [session.scope_key]);
  if (!needed) return null;
  return (
    <>
      <section className="info-callout" role="status" aria-label="管理操作需要二次验证">
        <p>登录仍然有效。这项管理操作需要二次验证，完成后请重新提交。</p>
        <button type="button" className="button secondary" onClick={() => setOpen(true)}>
          完成二次验证
        </button>
      </section>
      {open && (
        <Modal title="管理操作二次验证" onClose={() => setOpen(false)}>
          <Suspense
            fallback={
              <p role="status">
                <Spinner />
                正在读取验证设置…
              </p>
            }
          >
            <AdminMfaGate
              required
              scopeKey={session.scope_key}
              workspace
              challenged
              onVerified={onVerified}
              onLogout={onLogout}
            >
              <p role="status">验证已完成，请返回原页面确认内容并重新提交。</p>
              <button
                type="button"
                className="button primary"
                onClick={() => {
                  setOpen(false);
                  setNeeded(false);
                }}
              >
                返回原页面
              </button>
            </AdminMfaGate>
          </Suspense>
        </Modal>
      )}
    </>
  );
}
