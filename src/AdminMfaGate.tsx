import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { getAuthClient } from './auth';
import { errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import { enrollAdminTotp } from './admin-auth';
import './admin.css';

export default function AdminMfaGate({
  required,
  children,
  onVerified,
  onLogout,
  scopeKey,
  workspace = false,
  challenged = false,
}: {
  required: boolean;
  children: ReactNode;
  onVerified: () => Promise<void>;
  onLogout: () => Promise<void>;
  scopeKey: string;
  workspace?: boolean;
  challenged?: boolean;
}) {
  const [verified, setVerified] = useState(!required);
  const [loading, setLoading] = useState(required);
  const [busy, setBusy] = useState(false);
  const [factorId, setFactorId] = useState('');
  const [qr, setQr] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [forceChallenge, setForceChallenge] = useState(challenged);
  const attempt = useRef(0);
  useEffect(() => {
    const onRequired = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== scopeKey) return;
      attempt.current++;
      setVerified(false);
      setForceChallenge(true);
      setCode('');
      setQr('');
      setBusy(false);
      setRevision((value) => value + 1);
    };
    window.addEventListener('accounting:mfa-required', onRequired);
    return () => {
      attempt.current++;
      window.removeEventListener('accounting:mfa-required', onRequired);
    };
  }, [scopeKey]);
  useEffect(() => {
    if (!required && !forceChallenge) return;
    let disposed = false;
    setVerified(false);
    setLoading(true);
    setError('');
    const client = getAuthClient();
    void (async () => {
      if (!client) throw new Error('登录服务未就绪，请重新登录。');
      const result = await client.auth.mfa.getAuthenticatorAssuranceLevel();
      if (result.error) throw result.error;
      if (disposed) return;
      if (result.data.currentLevel === 'aal2' && !forceChallenge) {
        setVerified(true);
        return;
      }
      const factors = await client.auth.mfa.listFactors();
      if (factors.error) throw factors.error;
      if (!disposed)
        setFactorId(factors.data.totp.find((factor) => factor.status === 'verified')?.id ?? '');
    })()
      .catch(() => {
        if (!disposed) setError('无法读取身份验证设置，请重试或重新登录。');
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [required, revision, forceChallenge]);
  async function enroll() {
    const client = getAuthClient();
    if (!client || busy) return;
    const started = ++attempt.current;
    setBusy(true);
    setError('');
    try {
      const result = await enrollAdminTotp(client);
      if (started !== attempt.current) return;
      setFactorId(result.id);
      setQr(
        result.totp.qr_code.startsWith('data:')
          ? result.totp.qr_code
          : `data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.totp.qr_code)}`,
      );
    } catch {
      if (started === attempt.current)
        setError('暂时无法绑定验证器，请稍后重试。已完成的身份验证器不会被修改。');
    } finally {
      if (started === attempt.current) setBusy(false);
    }
  }
  async function verify() {
    const client = getAuthClient();
    if (!client || busy) return;
    const started = ++attempt.current;
    setBusy(true);
    setError('');
    try {
      const result = await client.auth.mfa.challengeAndVerify({ factorId, code });
      if (started !== attempt.current) return;
      setCode('');
      if (result.error) {
        setError('验证码不正确或已过期，请使用验证器中的最新 6 位验证码。');
        return;
      }
      setQr('');
      await onVerified();
      if (started === attempt.current) setVerified(true);
    } catch (failure) {
      if (started === attempt.current) setError(errorMessage(failure));
    } finally {
      if (started === attempt.current) setBusy(false);
    }
  }
  if (verified || (!required && !forceChallenge)) return children;
  return (
    <main className={workspace ? 'workspace-mfa' : 'admin-shell'}>
      <section className={workspace ? 'admin-mfa admin-mfa-inline' : 'admin-mfa'}>
        <ShieldCheck size={28} />
        <p className="eyebrow">ADMINISTRATOR VERIFICATION</p>
        <h1>验证管理员身份</h1>
        <p>管理企业和人员前，请通过验证器确认是本人操作。验证不会自动重新提交操作。</p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <div className="loading-block">
            <Spinner />
            正在读取验证设置…
          </div>
        ) : factorId ? (
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              void verify();
            }}
          >
            {qr && (
              <>
                <p>使用手机验证器扫描二维码，然后输入动态验证码完成绑定。</p>
                <img src={qr} alt="绑定管理员验证器的二维码" />
              </>
            )}
            <label>
              6 位动态验证码
              <input
                aria-label="6 位动态验证码"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                minLength={6}
                maxLength={6}
                required
                value={code}
                disabled={busy}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
              />
            </label>
            <button className="button primary" disabled={busy || code.length !== 6}>
              {busy && <Spinner />}
              {workspace ? '验证并继续' : '验证并进入后台'}
            </button>
          </form>
        ) : (
          <button className="button primary" disabled={busy} onClick={() => void enroll()}>
            {busy && <Spinner />}绑定身份验证器
          </button>
        )}
        {error && (
          <button
            className="text-button"
            disabled={busy}
            onClick={() => setRevision((value) => value + 1)}
          >
            重新读取验证设置
          </button>
        )}
        <div className="admin-form-actions">
          {!workspace && (
            <Link className="text-button" to="/workspace/home">
              返回工作台
            </Link>
          )}
          <button className="text-button" disabled={busy} onClick={() => void onLogout()}>
            退出登录
          </button>
        </div>
      </section>
    </main>
  );
}
