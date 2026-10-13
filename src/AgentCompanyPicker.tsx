import { useEffect, useRef, useState } from 'react';
import { Building2, Check, ChevronDown, LoaderCircle, Search, X } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import type { WorkspaceCompanies } from './workspace-types';

export function AgentCompanyPicker({
  session,
  value,
  onChange,
  disabled = false,
  endpoint = '/workspace/companies',
  label = '筛选待办企业',
  revision = 0,
}: {
  session: Session;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  endpoint?: string;
  label?: string;
  revision?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<WorkspaceCompanies | null>(null);
  const [selected, setSelected] = useState({ id: '', name: '', scope: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const scope = useRef(session.scope_key);
  scope.current = session.scope_key;
  const dataScope = useRef('');
  const epoch = useRef(0);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setExpanded(false);
    setData(null);
    setSelected({ id: '', name: '', scope: '' });
    setQuery('');
    setOffset(0);
  }, [session.scope_key, revision]);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    const request = ++epoch.current;
    const started = session.scope_key;
    setLoading(true);
    setData(null);
    setError('');
    const timeout = window.setTimeout(
      () => {
        void api<WorkspaceCompanies>(
          `${endpoint}?${new URLSearchParams({ q: query.trim(), limit: '20', offset: String(offset) })}`,
          session,
          { signal: controller.signal },
        )
          .then((next) => {
            if (
              !controller.signal.aborted &&
              scope.current === started &&
              request === epoch.current
            ) {
              dataScope.current = started;
              setData(next);
            }
          })
          .catch((caught) => {
            if (
              !controller.signal.aborted &&
              scope.current === started &&
              request === epoch.current
            ) {
              setData(null);
              setSelected({ id: '', name: '', scope: '' });
              setError(errorMessage(caught));
            }
          })
          .finally(() => {
            if (
              !controller.signal.aborted &&
              scope.current === started &&
              request === epoch.current
            )
              setLoading(false);
          });
      },
      query ? 180 : 0,
    );
    return () => {
      clearTimeout(timeout);
      controller.abort();
      epoch.current++;
    };
  }, [session, expanded, query, offset, endpoint, revision]);
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setExpanded(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setExpanded(false);
        root.current?.querySelector<HTMLButtonElement>('.agent-company-trigger')?.focus();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [expanded]);
  function select(id: string, name = '') {
    onChange(id);
    setSelected({ id, name, scope: session.scope_key });
    setExpanded(false);
  }
  const visibleData = dataScope.current === session.scope_key ? data : null;
  return (
    <div className="agent-company-picker" ref={root}>
      <button
        type="button"
        className="agent-company-trigger"
        aria-label={label}
        aria-expanded={expanded}
        aria-controls={`company-picker-${label}`}
        disabled={disabled}
        onClick={() => {
          setExpanded((v) => !v);
          setQuery('');
          setOffset(0);
        }}
      >
        <Building2 size={15} />
        <span>
          {value
            ? (selected.id === value && selected.scope === session.scope_key
                ? selected.name
                : '') || '指定企业 · 实时核权'
            : '全部授权企业'}
        </span>
        <ChevronDown size={14} />
      </button>
      {expanded && (
        <div
          className="agent-company-popover"
          id={`company-picker-${label}`}
          role="region"
          aria-label="企业快搜"
        >
          <label className="agent-search">
            <Search size={16} />
            <input
              autoFocus
              value={query}
              maxLength={200}
              placeholder="搜索企业名称"
              aria-label="搜索企业名称"
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
            />
            {query && (
              <button
                type="button"
                aria-label="清空企业搜索"
                onClick={() => {
                  setQuery('');
                  setOffset(0);
                }}
              >
                <X size={14} />
              </button>
            )}
          </label>
          <button type="button" className="agent-company-option" onClick={() => select('')}>
            <span>全部授权企业</span>
            {!value && <Check size={15} />}
          </button>
          {loading ? (
            <p className="agent-company-message">
              <LoaderCircle className="spinner" size={15} />
              读取授权企业…
            </p>
          ) : error ? (
            <p className="inline-error" role="alert">
              {error}
            </p>
          ) : visibleData?.companies.length ? (
            visibleData.companies.map((company) => (
              <button
                type="button"
                className="agent-company-option"
                key={company.id}
                onClick={() => select(company.id, company.name)}
              >
                <span>{company.name}</span>
                <small>{company.task_count} 项</small>
                {value === company.id && <Check size={15} />}
              </button>
            ))
          ) : (
            <p className="agent-company-message">没有匹配的授权企业</p>
          )}
          {visibleData && (
            <div className="agent-company-pages">
              <span>共 {visibleData.pagination.total} 家</span>
              <button
                type="button"
                disabled={loading || offset === 0}
                onClick={() => setOffset((v) => Math.max(0, v - 20))}
              >
                上一页
              </button>
              <button
                type="button"
                disabled={loading || !visibleData.pagination.has_more}
                onClick={() => setOffset((v) => v + 20)}
              >
                下一页
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
