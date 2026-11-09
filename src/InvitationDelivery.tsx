import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { useScopedQuery } from './useScopedQuery';
import { parseInvitationStatus } from './invitation-status';

const labels: Record<string, string> = {
  sending: '发送结果尚未确认，请核查；不会重复发送',
  accepted: '通道已接受，仍待本人激活',
  unknown: '发送结果未知，请核查；不会重复发送',
  not_sent: '通道确认未发送，可手动再次发送',
};
export function InvitationDelivery({ value, session }: { value: string; session: Session }) {
  return (
    <InvitationContent key={session.scope_key + ':' + value} value={value} session={session} />
  );
}
function InvitationContent({ value, session }: { value: string; session: Session }) {
  const [copied, setCopied] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [state, setState] = useState('');
  const [revoked, setRevoked] = useState(false);
  const live = useRef(true),
    locked = useRef(false),
    command = useRef(crypto.randomUUID());
  const requestId = new URLSearchParams(value.split('#')[1] ?? '').get('request_id') ?? '';
  const path = `/admin/account-requests/${encodeURIComponent(requestId)}`;
  const status = useScopedQuery(
    async (signal) => {
      return parseInvitationStatus(
        await api<unknown>(`${path}/invitation-delivery`, session, { signal }),
        requestId,
      );
    },
    session.scope_key + ':' + requestId,
  );
  useEffect(() => {
    live.current = true;
    const revoke = (e: Event) => {
      if ((e as CustomEvent<string>).detail === session.scope_key) {
        live.current = false;
        setRevoked(true);
      }
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      live.current = false;
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);
  if (revoked) return <p role="alert">授权已失效，请重新登录后查看邀请。</p>;
  const effective =
    state || (status.data?.delivery?.current_link ? status.data.delivery.state : '');
  const blocked = !!effective && effective !== 'not_sent';
  return (
    <section className="admin-activation-link" aria-label="交付账号邀请">
      <h3>将激活链接交给本人</h3>
      <p>
        一次性链接仅在本次操作后显示；重新生成将使旧链接失效。发送邮件不等于激活，也不授予账套或复核权限。
      </p>
      <textarea aria-label="账号激活链接" readOnly value={value} spellCheck={false} />
      <button
        className="button secondary"
        onClick={() => {
          void navigator.clipboard
            .writeText(value)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
      >
        {copied ? '已复制' : '复制激活链接'}
      </button>
      {status.data && (
        <p>
          收件邮箱：{status.data.email} · 邀请第 {status.data.request_attempt} 版
        </p>
      )}
      {!status.data?.enabled && <p>默认不会发送邮件；未启用受控通道时，请通过可信方式交给本人。</p>}
      {(error || status.error) && <p role="alert">{error || status.error}</p>}
      {effective && <p role="status">{labels[effective] ?? labels.unknown}</p>}
      <button
        className="button secondary"
        disabled={
          busy || !status.data?.enabled || blocked || status.data.status !== 'awaiting_activation'
        }
        onClick={async () => {
          if (locked.current || blocked || !status.data?.enabled) return;
          locked.current = true;
          setBusy(true);
          setError('');
          try {
            const r = await api<{ id: string; state: string }>(`${path}/send-invitation`, session, {
              method: 'POST',
              body: { activation_url: value, idempotency_key: command.current },
            });
            if (!live.current) return;
            if (!r.id || !labels[r.state]) throw new Error('发送回执无法核定，请先核查开通记录。');
            setState(r.state);
            if (r.state === 'not_sent') command.current = crypto.randomUUID();
          } catch (failure) {
            if (live.current) {
              setError(errorMessage(failure));
              setState('unknown');
            }
          } finally {
            if (live.current) {
              setBusy(false);
              locked.current = false;
            }
          }
        }}
      >
        {busy ? '正在发送…' : effective === 'not_sent' ? '再次发送邀请邮件' : '发送邀请邮件'}
      </button>
    </section>
  );
}
