import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { getAuthClient } from './auth';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { AuthConfig, Session } from './types';
import './admin.css';

export default function AccountActivation({
  config,
  session,
  loading,
  error: loginError,
  onLogin,
  onRefresh,
  onLogout,
}: {
  config: AuthConfig | null;
  session: Session | null;
  loading: boolean;
  error: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  // Never persist invitation secrets or include them in HTTP URLs/referrers.
  const [invitation, setInvitation] = useState(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    return {
      id: hash.get('request_id') ?? '',
      token: hash.get('token') ?? '',
      mode: hash.get('mode') ?? '',
      tokenHash: hash.get('token_hash') ?? '',
    };
  });
  useEffect(() => {
    window.history.replaceState(window.history.state, '', '/account-activation');
  }, []);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [verifiedInvite, setVerifiedInvite] = useState(false);
  const [useExistingAccount, setUseExistingAccount] = useState(false);
  const valid =
    !!invitation.id &&
    !!invitation.token &&
    ['invite', 'existing'].includes(invitation.mode) &&
    (invitation.mode !== 'invite' || !!invitation.tokenHash);
  async function activate() {
    const client = getAuthClient();
    if (!client || busy) return;
    setBusy(true);
    setError('');
    try {
      if (invitation.mode === 'invite' && !useExistingAccount) {
        if (!verifiedInvite) {
          const verified = await client.auth.verifyOtp({
            token_hash: invitation.tokenHash,
            type: 'invite',
          });
          if (verified.error) throw new Error('激活链接已失效或已使用，请联系管理员重新生成。');
          setVerifiedInvite(true);
        }
        const updated = await client.auth.updateUser({ password });
        if (updated.error) throw new Error('密码设置失败，请使用更长且符合登录服务要求的密码。');
      }
      setPassword('');
      setConfirm('');
      const current = await client.auth.getSession();
      if (!current.data.session) throw new Error('请先使用受邀邮箱登录，再接受授权。');
      await api(
        `/admin/account-requests/${encodeURIComponent(invitation.id)}/accept`,
        current.data.session.access_token,
        { method: 'POST', body: { token: invitation.token } },
      );
      await onRefresh();
      setInvitation({ id: '', token: '', mode: '', tokenHash: '' });
      setDone(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="admin-shell">
      <section className="admin-mfa">
        <ShieldCheck size={28} />
        <p className="eyebrow">ACCOUNT ACTIVATION</p>
        <h1>{done ? '账号已激活' : '确认加入工作空间'}</h1>
        {done ? (
          <>
            <p>授权已生效，可以进入工作台查看分配给你的企业和任务。</p>
            <Link className="button primary" to="/workspace/home">
              进入工作台
            </Link>
          </>
        ) : !valid ? (
          <>
            <p role="alert">激活链接不完整。请重新打开管理员提供的完整链接。</p>
            <Link className="text-button" to="/">
              返回登录
            </Link>
          </>
        ) : (
          <>
            {(error || loginError) && (
              <p className="form-error" role="alert">
                {error || loginError}
              </p>
            )}
            {!config ? (
              <div className="loading-block">
                <Spinner />
                正在连接登录服务…
              </div>
            ) : invitation.mode === 'invite' && !useExistingAccount ? (
              <form
                className="admin-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void activate();
                }}
              >
                <p>为自己的账号设置密码。此操作会接受管理员为你分配的企业或事务所授权。</p>
                <label>
                  设置密码
                  <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={12}
                    value={password}
                    disabled={busy || loading}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </label>
                <label>
                  再次输入密码
                  <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={12}
                    value={confirm}
                    disabled={busy || loading}
                    onChange={(event) => setConfirm(event.target.value)}
                  />
                </label>
                <p className="field-help">至少 12 位，建议使用密码管理器生成。</p>
                <button
                  className="button primary"
                  disabled={busy || loading || password.length < 12 || password !== confirm}
                >
                  {busy && <Spinner />}设置密码并接受授权
                </button>
                <button
                  className="text-button"
                  type="button"
                  disabled={busy || loading}
                  onClick={() => {
                    setPassword('');
                    setConfirm('');
                    setError('');
                    setUseExistingAccount(true);
                  }}
                >
                  已设置密码或链接已使用？使用账号登录继续
                </button>
                <Link className="text-button" to="/account-recovery">
                  密码设置中断或忘记密码？本人找回密码
                </Link>
              </form>
            ) : session ? (
              <>
                <p>
                  当前登录：{session.user_name}
                  。接受后将加入管理员指定的范围；已有其他企业权限继续保留。
                </p>
                <button
                  className="button primary"
                  disabled={busy || loading}
                  onClick={() => void activate()}
                >
                  {busy && <Spinner />}确认接受授权
                </button>
                <button className="text-button" disabled={busy} onClick={() => void onLogout()}>
                  切换登录账号
                </button>
              </>
            ) : (
              <form
                className="admin-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  const secret = password;
                  setPassword('');
                  void onLogin(email.trim(), secret);
                }}
              >
                <p>请使用受邀邮箱原有的账号和密码登录。此邀请不会修改已有密码。</p>
                <label>
                  受邀邮箱
                  <input
                    type="email"
                    autoComplete="username"
                    required
                    value={email}
                    disabled={loading}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </label>
                <label>
                  密码
                  <input
                    type="password"
                    autoComplete="current-password"
                    required
                    value={password}
                    disabled={loading}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                </label>
                <button className="button primary" disabled={loading || !email || !password}>
                  {loading && <Spinner />}登录后接受授权
                </button>
                <Link className="text-button" to="/account-recovery">
                  找回密码（完成后重新打开本邀请链接）
                </Link>
              </form>
            )}
          </>
        )}
      </section>
    </main>
  );
}
