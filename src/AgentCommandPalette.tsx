import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Building2,
  Command,
  CornerDownLeft,
  LoaderCircle,
  Search,
  X,
} from 'lucide-react';
import { api, errorMessage } from './api';
import type { Book, Session, Workbench } from './types';

export interface AgentCommand {
  id: string;
  label: string;
  description: string;
  run: () => void;
}

export function AgentCommandPalette({
  session,
  commands,
  canSearchBooks,
  onOpenBook,
  onClose,
}: {
  session: Session;
  commands: AgentCommand[];
  canSearchBooks: boolean;
  onOpenBook: (book: Book) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(''),
    [books, setBooks] = useState<Book[]>([]),
    [loading, setLoading] = useState(false),
    [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null),
    dialog = useRef<HTMLDivElement>(null),
    epoch = useRef(0),
    scope = useRef(session.scope_key);
  scope.current = session.scope_key;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null,
      overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    input.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      if (before?.isConnected) before.focus();
      epoch.current++;
    };
  }, []);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const id = ++epoch.current,
        started = session.scope_key;
      setBooks([]);
      setError('');
      if (!canSearchBooks || !query.trim()) {
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const value = await api<Workbench>(
          '/workbench?' + new URLSearchParams({ search: query.trim(), limit: '10', offset: '0' }),
          session,
          { signal },
        );
        if (!signal.aborted && id === epoch.current && started === scope.current)
          setBooks(value.books);
      } catch (failure) {
        if (!signal.aborted && id === epoch.current && started === scope.current) {
          setBooks([]);
          setError(errorMessage(failure));
        }
      } finally {
        if (!signal.aborted && id === epoch.current && started === scope.current) setLoading(false);
      }
    },
    [query, session, canSearchBooks],
  );
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      epoch.current++;
    };
  }, [load]);
  useEffect(() => {
    const revoke = () => {
      epoch.current++;
      setBooks([]);
      setQuery('');
      setError('授权已更新，请关闭后重新查找。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, []);
  function key(event: React.KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
    if (event.key === 'Tab' && dialog.current) {
      const elements = Array.from(
        dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled)'),
      ).filter((item) => item.getClientRects().length);
      if (event.shiftKey && document.activeElement === elements[0]) {
        event.preventDefault();
        elements[elements.length - 1]?.focus();
      } else if (!event.shiftKey && document.activeElement === elements[elements.length - 1]) {
        event.preventDefault();
        elements[0]?.focus();
      }
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const buttons = Array.from(
        dialog.current?.querySelectorAll<HTMLButtonElement>('.agent-command-result') ?? [],
      );
      const index = buttons.findIndex((button) => button === document.activeElement);
      if (buttons.length) {
        event.preventDefault();
        buttons[
          (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
        ]?.focus();
      }
    }
  }
  const filtered = commands.filter((command) =>
    (command.label + command.description).includes(query.trim()),
  );
  return (
    <div
      className="agent-command-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-command-title"
        className="agent-command-dialog"
        onKeyDown={key}
      >
        <div className="agent-command-input">
          <Search size={21} />
          <label className="sr-only" htmlFor="agent-command-search">
            查找企业或工作入口
          </label>
          <input
            ref={input}
            id="agent-command-search"
            placeholder="搜索企业名称，或输入“复核”“后台”…"
            maxLength={120}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="icon-button" onClick={onClose} aria-label="关闭查找">
            <X size={18} />
          </button>
        </div>
        <div className="agent-command-results">
          <h2 id="agent-command-title">快速进入工作</h2>
          {filtered.map((command) => (
            <button
              className="agent-command-result"
              key={command.id}
              onClick={() => {
                onClose();
                command.run();
              }}
            >
              <span>
                <strong>{command.label}</strong>
                <small>{command.description}</small>
              </span>
              <ArrowUpRight size={17} />
            </button>
          ))}
          {loading && (
            <p role="status">
              <LoaderCircle className="spinner" size={16} />
              正在查找授权企业…
            </p>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {books.length > 0 && <h3>当前授权账套</h3>}
          {books.map((book) => (
            <button
              className="agent-command-result"
              key={book.id}
              onClick={() => {
                onClose();
                onOpenBook(book);
              }}
            >
              <Building2 size={17} />
              <span>
                <strong>{book.company_name || book.name}</strong>
                <small>
                  {book.name} · {book.period} ·{' '}
                  {book.period_status === 'closed' ? '已关期' : '开放期间'}
                </small>
              </span>
              <CornerDownLeft size={15} />
            </button>
          ))}
          {query.trim() && !loading && !error && !books.length && !filtered.length && (
            <p className="agent-command-empty">当前工作范围内没有匹配结果。请调整搜索。</p>
          )}
        </div>
        <footer>
          <span>
            <Command size={12} /> / Ctrl K 查找
          </span>
          <span>↑ ↓ 选择 · Enter 打开 · Esc 返回</span>
        </footer>
      </div>
    </div>
  );
}
