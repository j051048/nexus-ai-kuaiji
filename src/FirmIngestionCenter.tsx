import { useEffect, useRef, useState } from 'react';
import { Cable, KeyRound, Plus, RefreshCw } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import type { Session } from './types';
import './firm-ingestion.css';

type Connection = {
  id: string;
  company_id: string;
  kind: string;
  name: string;
  status: string;
  last_cursor: string;
  created_at: string;
  token?: string;
};
type Run = {
  id: string;
  connection_name: string;
  status: string;
  attempt: number;
  cursor: string;
  error_code: string;
  error_detail: string;
  created_at: string;
};

const kinds = [
  ['csv', 'CSV'],
  ['xlsx', 'Excel'],
  ['email', '邮件附件'],
  ['sftp', 'SFTP'],
  ['webhook', 'Webhook'],
  ['bank', '银行'],
  ['invoice', '电子发票'],
  ['erp', 'ERP / T+'],
] as const;

export function FirmIngestionCenter({ session }: { session: Session }) {
  return <IngestionWorkspace key={session.scope_key} session={session} />;
}

function IngestionWorkspace({ session }: { session: Session }) {
  const [companies, setCompanies] = useState<{ id: string; name: string }[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [kind, setKind] = useState('webhook');
  const [name, setName] = useState('');
  const [token, setToken] = useState<{ name: string; value: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const active = useRef(true);
  const epoch = useRef(0);
  const command = useRef(false);

  useEffect(() => {
    active.current = true;
    const invalidate = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      epoch.current += 1;
      clearData();
      setLoading(false);
      setBusy('');
      setError('当前授权可能已更新，已清除页面数据。请重新读取。');
    };
    window.addEventListener('accounting:forbidden', invalidate);
    return () => {
      active.current = false;
      epoch.current += 1;
      window.removeEventListener('accounting:forbidden', invalidate);
    };
  }, [session.scope_key]);

  function clearData() {
    setCompanies([]);
    setConnections([]);
    setRuns([]);
    setCompanyId('');
    setToken(null);
  }

  useEffect(() => {
    const controller = new AbortController();
    const stamp = ++epoch.current;
    const current = () => active.current && !controller.signal.aborted && stamp === epoch.current;
    setLoading(true);
    setError('');
    setCompanies([]);
    setConnections([]);
    setRuns([]);
    void Promise.all([
      api<{ companies: { id: string; name: string }[] }>('/companies', session, {
        signal: controller.signal,
      }),
      api<{ connections: Connection[]; runs: Run[] }>('/firm/ingestion', session, {
        signal: controller.signal,
      }),
    ])
      .then(([companyData, ingestion]) => {
        if (!current()) return;
        setCompanies(companyData.companies);
        setCompanyId((selected) =>
          companyData.companies.some((company) => company.id === selected)
            ? selected
            : companyData.companies[0]?.id || '',
        );
        setConnections(ingestion.connections);
        setRuns(ingestion.runs);
      })
      .catch((failure) => {
        if (
          current() ||
          (active.current && failure instanceof ApiError && failure.status === 403)
        ) {
          clearData();
          setError(errorMessage(failure));
        }
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => controller.abort();
  }, [session, revision]);

  async function create() {
    if (command.current || loading || !companyId || !name.trim()) return;
    command.current = true;
    const stamp = ++epoch.current;
    const current = () => active.current && stamp === epoch.current;
    setBusy('create');
    setError('');
    setToken(null);
    try {
      const value = await api<Connection>('/firm/ingestion/connections', session, {
        method: 'POST',
        body: { company_id: companyId, kind, name: name.trim(), config: {} },
      });
      if (!current()) return;
      setToken(value.token ? { name: value.name, value: value.token } : null);
      setName('');
      setRevision((current) => current + 1);
    } catch (failure) {
      if (current() || (active.current && failure instanceof ApiError && failure.status === 403)) {
        clearData();
        setError(errorMessage(failure));
      }
    } finally {
      command.current = false;
      if (current()) setBusy('');
    }
  }

  async function rotate(connection: Connection) {
    if (command.current || loading) return;
    command.current = true;
    const stamp = ++epoch.current;
    const current = () => active.current && stamp === epoch.current;
    setBusy('rotate:' + connection.id);
    setError('');
    setToken(null);
    try {
      const value = await api<Connection>(
        `/firm/ingestion/connections/${encodeURIComponent(connection.id)}/rotate`,
        session,
        { method: 'POST' },
      );
      if (!current()) return;
      setToken(value.token ? { name: value.name, value: value.token } : null);
      setRevision((current) => current + 1);
    } catch (failure) {
      if (current() || (active.current && failure instanceof ApiError && failure.status === 403)) {
        clearData();
        setError(errorMessage(failure));
      }
    } finally {
      command.current = false;
      if (current()) setBusy('');
    }
  }

  return (
    <main className="firm-ingestion">
      <header>
        <div>
          <p className="eyebrow">INGESTION / OBSERVABLE FIRST</p>
          <h1>集成与数据接入中心</h1>
          <p>连接状态、最近同步、失败记录和重试集中在同一处。</p>
        </div>
        <button
          className="button secondary"
          type="button"
          disabled={loading || !!busy}
          onClick={() => {
            setToken(null);
            setRevision((current) => current + 1);
          }}
        >
          <RefreshCw size={15} />
          刷新
        </button>
      </header>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {token && (
        <section className="ingestion-token" aria-live="polite">
          <KeyRound size={17} />
          <div>
            <strong>{token.name} 新凭证</strong>
            <code>{token.value}</code>
            <small>只显示一次，请存入客户技术负责人的密钥管理系统。</small>
          </div>
        </section>
      )}
      <div className="ingestion-grid">
        <section>
          <header>
            <h2>数据连接</h2>
            {loading && <Spinner />}
          </header>
          <div className="ingestion-connections">
            {connections.map((connection) => (
              <article key={connection.id}>
                <span className="ingestion-kind">{connection.kind.toUpperCase()}</span>
                <div>
                  <strong>{connection.name}</strong>
                  <p>
                    {connection.status} · 最近游标 {connection.last_cursor || '未开始'}
                  </p>
                </div>
                <button
                  className="text-button"
                  type="button"
                  disabled={!!busy || loading}
                  onClick={() => void rotate(connection)}
                >
                  <KeyRound size={14} />
                  轮换凭证
                </button>
              </article>
            ))}
            {!connections.length && !loading && <p className="empty-state">还没有数据连接。</p>}
          </div>
          <div className="ingestion-create">
            <h2>新增连接</h2>
            <label>
              客户企业
              <select
                value={companyId}
                disabled={!!busy || loading}
                onChange={(event) => setCompanyId(event.target.value)}
              >
                {companies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              来源类型
              <select
                value={kind}
                disabled={!!busy || loading}
                onChange={(event) => setKind(event.target.value)}
              >
                {kinds.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              连接名称
              <input
                value={name}
                disabled={!!busy || loading}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <button
              className="button primary"
              type="button"
              disabled={!companyId || !name.trim() || !!busy || loading}
              onClick={() => void create()}
            >
              <Plus size={15} />
              创建连接与接入凭证
            </button>
          </div>
        </section>
        <section>
          <header>
            <h2>最近接入记录</h2>
            <Cable size={17} />
          </header>
          <div className="ingestion-runs">
            {runs.map((run) => (
              <article key={run.id} className={run.status}>
                <strong>{run.connection_name}</strong>
                <span>
                  {run.status} · 尝试 {run.attempt}
                </span>
                <small>{run.error_detail || run.cursor || '已接收，等待处理'}</small>
              </article>
            ))}
            {!runs.length && <p className="empty-state">还没有接入记录。</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
