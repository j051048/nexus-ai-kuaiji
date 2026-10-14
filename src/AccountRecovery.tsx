import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { LockKeyhole } from 'lucide-react';
import { getAuthClient } from './auth';
import { errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { AuthConfig } from './types';
import './admin.css';

export default function AccountRecovery({
  config,
  onLogout,
}: {
  config: AuthConfig | null;
  onLogout: () => Promise<void>;
}) {
  const [recovery, setRecovery] = useState(() => {
    const values = new URLSearchParams(window.location.hash.slice(1));
    return {
      access: values.get('access_token') ?? '',
      refresh: values.get('refresh_token') ?? '',
      type: values.get('type') ?? '',
      invalid: values.has('error') || values.has('error_code'),
    };
  });
  useEffect(() => {
    window.history.replaceState(window.history.state, '', '/account-recovery');
  }, []);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [verified, setVerified] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const valid = recovery.type === 'recovery' && !!recovery.access && !!recovery.refresh;
  const partial =
    recovery.invalid || ((!!recovery.type || !!recovery.access || !!recovery.refresh) && !valid);
  async function send() {
    const client = getAuthClient();
    if (!client || busy) return;
    setBusy(true);
    setError('');
    try {
      // Only the current trusted app origin is allowed; do not accept arbitrary
      // redirect destinations from query parameters or invitation payloads.
      const result = await client.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: `${window.location.origin}/account-recovery`,
      });
      if (result.error)
        throw new Error('暂时无法发送重置邮件，请稍后重试，或请管理员检查邮件服务配置。');
      setSent(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    const client = getAuthClient();
    if (!client || busy || !valid) return;
    setBusy(true);
    setError('');
    try {
      if (!verified) {
        const accepted = await client.auth.setSession({
          access_token: recovery.access,
          refresh_token: recovery.refresh,
        });
        if (accepted.error || !accepted.data.session)
          throw new Error('重置链接已失效，请返回找回密码页面重新申请邮件。');
        setVerified(true);
      }
      const result = await client.auth.updateUser({ password });
      if (result.error)
        throw new Error('密码更新失败，请使用更长且符合登录服务要求的密码，然后重试。');
      setPassword('');
      setConfirm('');
      await onLogout();
      setRecovery({ access: '', refresh: '', type: '', invalid: false });
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
        <LockKeyhole size={28} />
        <p className="eyebrow">ACCOUNT RECOVERY</p>
        <h1>{done ? '密码已更新' : valid ? '设置新的登录密码' : '本人找回密码'}</h1>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {done ? (
          <>
            <p>
              请使用新密码重新登录。若正在激活新账号，请重新打开管理员提供的邀请链接，选择“使用账号登录继续”，再接受企业授权。
            </p>
            <Link className="button primary" to="/">
              返回登录
            </Link>
          </>
        ) : !config ? (
          <div className="loading-block">
            <Spinner />
            正在连接登录服务…
          </div>
        ) : config.mode !== 'supabase' ? (
          <p>当前为本地验证环境，未连接邮件登录服务。</p>
        ) : valid ? (
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <p>此重置链接只用于验证你本人的身份，不会新增企业权限或改变其他人的账号。</p>
            <label>
              新密码
              <input
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                value={password}
                disabled={busy}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <label>
              再次输入新密码
              <input
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                value={confirm}
                disabled={busy}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </label>
            <p className="field-help">至少 12 位，建议使用密码管理器生成。</p>
            <button
              className="button primary"
              disabled={busy || password.length < 12 || password !== confirm}
            >
              {busy && <Spinner />}保存新密码
            </button>
          </form>
        ) : sent ? (
          <>
            <p role="status">
              如果该邮箱对应有效账号，你将收到重置邮件。请检查收件箱及垃圾邮件，并在有效期内打开链接。
            </p>
            <p>完成后重新打开原邀请链接，或返回正常登录入口。请勿将重置链接转发他人。</p>
            <button className="text-button" onClick={() => setSent(false)}>
              重新填写邮箱
            </button>
          </>
        ) : (
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            {partial && (
              <p className="form-error" role="alert">
                重置链接不完整或已失效，请重新申请重置邮件。
              </p>
            )}
            <p>由你主动发送重置邮件到自己的登录邮箱。管理员不能代你查看密码或获取重置链接。</p>
            <label>
              登录邮箱
              <input
                type="email"
                autoComplete="username"
                required
                value={email}
                disabled={busy}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            <button className="button primary" disabled={busy || !email.trim()}>
              {busy && <Spinner />}发送密码重置邮件
            </button>
          </form>
        )}
        {!done && (
          <p>
            <Link className="text-button" to="/">
              返回登录
            </Link>
          </p>
        )}
      </section>
    </main>
  );
}
