import { useState } from 'react';
import './auth-screen.css';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  Building2,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  Smartphone,
  TriangleAlert,
} from 'lucide-react';
import { isStandalonePwa, openPwaInstallGuide } from './pwa-install-trigger';
import type { AuthConfig } from './types';

export function AuthScreen({
  config,
  loading,
  error,
  onLogin,
  onRetry,
  admin = false,
}: {
  config: AuthConfig | null;
  loading: boolean;
  error: string;
  onLogin: (email: string, password: string) => Promise<void>;
  onRetry: () => Promise<void>;
  admin?: boolean;
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
            衡账<span>集团共享财务中心</span>
          </span>
        </div>
        <div className="auth-story-copy">
          <p className="eyebrow">集团共享财务中台 · 智能辅助与纯手工双轨</p>
          <h1>
            一套中台。
            <br />
            管好集团五家公司，
            <br />
            每笔账清晰可核。
          </h1>
          <p>
            子公司报销审批、共享中心制单复核、
            <br />
            与集团经营大屏，在一个工作空间顺畅协同。
          </p>
        </div>
        <div className="auth-story-foot">
          <ShieldCheck size={18} />
          <span>按子公司与岗位分权隔离 · 断网纯手工照常做账</span>
        </div>
      </section>
      <section className="auth-form-side">
        <div className="auth-card">
          <span className="auth-symbol">
            <LockKeyhole size={25} />
          </span>
          <p className="eyebrow">
            {admin ? '集团组织与权限管理' : '集团共享财务工作空间'}
          </p>
          <h2>
            {admin
              ? '登录共享中心管理后台'
              : config?.mode === 'supabase'
                ? '登录集团共享财务中心'
                : '连接集团共享财务中心'}
          </h2>
          <p className="auth-intro">
            {admin
              ? '使用工作账号管理集团旗下子公司档案、岗位分工与智能审核规则。'
              : '登录您的岗位账号，系统自动进入对应的共享中心工作台或子公司门户。'}
          </p>
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
                const submitted = password;
                setPassword('');
                void onLogin(email.trim(), submitted);
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
                {admin ? '登录管理后台' : '登录工作空间'}
              </button>
              <p className="auth-account-help">
                还没有账户？请联系管理员。<Link to="/account-recovery">忘记密码</Link>
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
          <div className="auth-bottom-links">
            <Link className="text-button" to={admin ? '/' : '/admin'}>
              {admin ? '返回工作台登录' : '管理员入口'}
            </Link>
            {!isStandalonePwa() && (
              <button type="button" className="text-button" onClick={openPwaInstallGuide}>
                <Smartphone size={14} aria-hidden="true" />
                添加到手机桌面
              </button>
            )}
          </div>
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
  allowCreate = false,
}: {
  name: string;
  loading: boolean;
  error: string;
  onCreate: (name: string) => Promise<void>;
  onLogout: () => Promise<void>;
  allowCreate?: boolean;
}) {
  const [firmName, setFirmName] = useState('');
  return (
    <div className="access-empty">
      <span className="auth-symbol">
        <ShieldCheck size={30} />
      </span>
      <p className="eyebrow">工作空间访问权限</p>
      <h1>尚未获得集团共享中心或子公司授权</h1>
      <p>
        {name}，请联系集团共享财务中心管理员，将您的登录邮箱加入对应子公司或财务岗位。
        {allowCreate && (
          <>
            <br />
            如果您负责初始化集团共享财务中心，可以在这里创建集团工作空间。
          </>
        )}
      </p>
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
      {allowCreate && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (firmName.trim()) void onCreate(firmName.trim());
          }}
        >
          <label className="field-label" htmlFor="first-firm-name">
            集团共享中心名称
          </label>
          <input
            id="first-firm-name"
            value={firmName}
            onChange={(event) => setFirmName(event.target.value)}
            maxLength={120}
            placeholder="例如：星海集团共享财务中心"
            disabled={loading}
            required
          />
          <button className="button primary" disabled={loading || !firmName.trim()}>
            {loading ? <LoaderCircle className="spinner" size={16} /> : <Building2 size={16} />}
            创建集团共享中心
          </button>
        </form>
      )}
      <button className="text-button" disabled={loading} onClick={() => void onLogout()}>
        退出登录
      </button>
    </div>
  );
}
