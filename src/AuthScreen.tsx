import { useState } from 'react';
import {
  ArrowRight,
  Building2,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import type { AuthConfig } from './types';

export function AuthScreen({
  config,
  loading,
  error,
  onLogin,
  onRetry,
}: {
  config: AuthConfig | null;
  loading: boolean;
  error: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onRetry: () => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  return (
    <div className="auth-shell">
      <section className="auth-story">
        <div className="brand auth-brand">
          <span className="brand-mark">
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">
            衡账<span>HENGZHANG · AI</span>
          </span>
        </div>
        <div className="auth-story-copy">
          <p className="eyebrow">A CLEARER WAY TO ACCOUNT</p>
          <h1>
            专注判断。
            <br />
            让每一笔账，
            <br />
            都有据可循。
          </h1>
          <p>
            客户企业、原始凭据与审核记录，
            <br />
            在一个清晰的工作空间相连。
          </p>
        </div>
        <div className="auth-story-foot">
          <ShieldCheck size={18} />
          <span>按事务所与账套授权访问 · 会计审核后导出</span>
        </div>
      </section>
      <section className="auth-form-side">
        <div className="auth-card">
          <span className="auth-symbol">
            <LockKeyhole size={25} />
          </span>
          <p className="eyebrow">YOUR ACCOUNTING WORKSPACE</p>
          <h2>{config?.mode === 'supabase' ? '登录会计工作空间' : '连接会计工作空间'}</h2>
          <p className="auth-intro">使用管理员为你开通的账户，访问获授权的企业与账套。</p>
          {error && (
            <p className="auth-error" role="alert">
              <TriangleAlert size={17} />
              {error}
            </p>
          )}
          {config?.mode === 'supabase' ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void onLogin(email.trim(), password);
              }}
            >
              <label className="field-label" htmlFor="login-email">
                工作邮箱
              </label>
              <input
                id="login-email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@company.com"
                disabled={loading}
                required
              />
              <label className="field-label" htmlFor="login-password">
                密码
              </label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="输入账户密码"
                disabled={loading}
                required
              />
              <button
                className="button primary auth-submit"
                type="submit"
                disabled={loading || !email.trim() || !password}
              >
                {loading ? (
                  <LoaderCircle className="spinner" size={16} />
                ) : (
                  <ArrowRight size={16} />
                )}
                登录工作空间
              </button>
              <p className="auth-account-help">
                还没有账户或忘记密码？请联系管理员。
                <br />
                关闭或刷新页面后需重新登录。
              </p>
            </form>
          ) : (
            <button
              className="button primary auth-submit"
              type="button"
              onClick={() => void onRetry()}
              disabled={loading}
            >
              {loading ? <LoaderCircle className="spinner" size={16} /> : <Building2 size={16} />}
              {loading ? '正在连接…' : '重新连接'}
            </button>
          )}
          <p className="auth-footer">中国大陆企业 · 人民币 · 辅助记账验证</p>
        </div>
      </section>
    </div>
  );
}

export function NoAuthorization({
  name,
  loading,
  error,
  onCreate,
  onLogout,
}: {
  name: string;
  loading: boolean;
  error: string;
  onCreate: (name: string) => Promise<void>;
  onLogout: () => Promise<void>;
}) {
  const [firmName, setFirmName] = useState('');
  return (
    <div className="access-empty">
      <span className="auth-symbol">
        <ShieldCheck size={30} />
      </span>
      <p className="eyebrow">WORKSPACE ACCESS</p>
      <h1>尚未获得企业或事务所授权</h1>
      <p>
        {name}，请让管理员将你的登录邮箱加入工作空间。
        <br />
        如果你负责新建事务所，可以在这里创建独立的工作空间。
      </p>
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (firmName.trim()) void onCreate(firmName.trim());
        }}
      >
        <label className="field-label" htmlFor="first-firm-name">
          事务所名称
        </label>
        <input
          id="first-firm-name"
          value={firmName}
          onChange={(event) => setFirmName(event.target.value)}
          maxLength={120}
          placeholder="请输入事务所名称"
          disabled={loading}
          required
        />
        <button className="button primary" disabled={loading || !firmName.trim()}>
          {loading ? <LoaderCircle className="spinner" size={16} /> : <Building2 size={16} />}
          创建事务所
        </button>
      </form>
      <button className="text-button" disabled={loading} onClick={() => void onLogout()}>
        退出登录
      </button>
    </div>
  );
}
