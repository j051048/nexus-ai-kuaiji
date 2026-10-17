import { Component, useEffect, type ErrorInfo, type ReactNode } from 'react';
import { useRouteError } from 'react-router-dom';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import { isChunkLoadError, recoverFromChunkError } from './preload-recovery';

function reloadWithoutCache() {
  const url = new URL(window.location.href);
  url.searchParams.set('_deploy_reload', String(Date.now()));
  window.location.replace(url.toString());
}

function FatalErrorPage({ error }: { error: unknown }) {
  const chunkFailure = isChunkLoadError(error);
  return (
    <main className="fatal-error-page">
      <section className="fatal-error-card" role="alert">
        <span className="fatal-error-icon">
          <TriangleAlert size={28} />
        </span>
        <p className="eyebrow">APPLICATION RECOVERY</p>
        <h1>{chunkFailure ? '页面版本已更新' : '页面暂时无法打开'}</h1>
        <p>
          {chunkFailure
            ? '浏览器仍在使用旧版页面资源。重新加载后会自动获取最新版本，已保存的账务数据不会受影响。'
            : '系统遇到了未预期的页面错误。请重新加载；如仍失败，请把页面标题和时间告知管理员。'}
        </p>
        <div className="fatal-error-actions">
          <button type="button" className="button primary" onClick={reloadWithoutCache}>
            <RefreshCw size={15} />
            重新加载最新版本
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={() => {
              window.location.replace('/');
            }}
          >
            返回登录页
          </button>
        </div>
        <small>错误编号：{chunkFailure ? 'STATIC_RESOURCE_STALE' : 'UI_RUNTIME_ERROR'}</small>
      </section>
    </main>
  );
}

export class AppErrorBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state = { error: null as unknown };

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled application error', error, info.componentStack);
    recoverFromChunkError(error);
  }

  render() {
    if (this.state.error) return <FatalErrorPage error={this.state.error} />;
    return this.props.children;
  }
}

export function AppRouteError() {
  const error = useRouteError();
  useEffect(() => {
    recoverFromChunkError(error);
  }, [error]);
  return <FatalErrorPage error={error} />;
}
