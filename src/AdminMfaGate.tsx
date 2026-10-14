import { useEffect, useState, type ReactNode } from 'react';
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
}: {
  required: boolean;
  children: ReactNode;
  onVerified: () => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  const [verified, setVerified] = useState(!required);
  const [loading, setLoading] = useState(required);
  const [busy, setBusy] = useState(false);
  const [factorId, setFactorId] = useState('');
  const [qr, setQr] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!required) return;
    let disposed = false;
    setLoading(true);
    setError('');
    const client = getAuthClient();
    void (async () => {
      if (!client) throw new Error('登录服务未就绪，请重新登录。');
      const result = await client.auth.mfa.getAuthenticatorAssuranceLevel();
      if (result.error) throw result.error;
      if (disposed) return;
      if (result.data.currentLevel === 'aal2') {
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
  }, [required, revision]);
  async function enroll() {
    const client = getAuthClient();
    if (!client || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await enrollAdminTotp(client);
      setFactorId(result.id);
      setQr(
        result.totp.qr_code.startsWith('data:')
          ? result.totp.qr_code
          : `data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.totp.qr_code)}`,
      );
    } catch {
      setError('暂时无法绑定验证器，请稍后重试。已完成的身份验证器不会被修改。');
    } finally {
      setBusy(false);
    }
  }
  async function verify() {
    const client = getAuthClient();
    if (!client || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await client.auth.mfa.challengeAndVerify({ factorId, code });
      setCode('');
      if (result.error) {
        setError('验证码不正确或已过期，请使用验证器中的最新 6 位验证码。');
        return;
      }
      setQr('');
      await onVerified();
      setVerified(true);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  if (verified || !required) return children;
  return (
    <main className="admin-shell">
      <section className="admin-mfa">
        <ShieldCheck size={28} />
        <p className="eyebrow">ADMINISTRATOR VERIFICATION</p>
        <h1>验证管理员身份</h1>
        <p>管理企业和人员前，请通过验证器确认是本人操作。</p>
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
              {busy && <Spinner />}验证并进入后台
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
          <Link className="text-button" to="/workspace/home">
            返回工作台
          </Link>
          <button className="text-button" disabled={busy} onClick={() => void onLogout()}>
            退出登录
          </button>
        </div>
      </section>
    </main>
  );
}
