import { Check, ChevronRight, Search, TriangleAlert, X } from 'lucide-react';
import { EmptyState, PageControls } from './AppPrimitives';
import type { Book, Pagination } from './types';

export function AccountingBookList({
  books,
  bookId,
  search,
  filtered,
  busy,
  loading,
  pagination,
  onSearch,
  onChoose,
  onPage,
}: {
  books: Book[];
  bookId: string;
  search: string;
  filtered: boolean;
  busy: boolean;
  loading: boolean;
  pagination?: Pagination;
  onSearch: (value: string) => void;
  onChoose: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  return (
    <section className="panel books-panel">
      <div className="panel-heading">
        <div>
          <h2>
            客户账套<span className="count-label">{books.length}</span>
          </h2>
          <p>按企业分开处理，审核状态一眼可见</p>
        </div>
        <label className="search-box">
          <Search size={16} />
          <input
            placeholder="搜索企业名称"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            aria-label="搜索企业名称"
          />
          {search && (
            <button
              type="button"
              className="icon-button"
              aria-label="清空搜索"
              onClick={() => onSearch('')}
            >
              <X size={13} />
            </button>
          )}
        </label>
      </div>
      <div className="book-list" role="list" aria-label="客户企业">
        {books.length ? (
          books.map((book, index) => (
            <button
              role="listitem"
              key={book.id}
              type="button"
              disabled={busy}
              className={`book-row ${bookId === book.id ? 'selected' : ''}`}
              onClick={() => onChoose(book.id)}
              aria-pressed={bookId === book.id}
            >
              <span className={`company-icon tone-${index % 3}`}>{book.name.slice(0, 1)}</span>
              <span className="book-description">
                <strong>{book.name}</strong>
                <span>
                  {book.company_name && `${book.company_name} · `}
                  {book.period} 会计期间<span className="inline-dot">·</span>人民币账套
                </span>
              </span>
              <span className="book-count">
                <b>{book.pending_count}</b>
                <small>待审核</small>
              </span>
              <span className={`book-exception ${book.exception_count ? 'has-exception' : ''}`}>
                {book.exception_count ? (
                  <>
                    <TriangleAlert size={12} />
                    {book.exception_count} 项异常
                  </>
                ) : (
                  <>
                    <Check size={12} />
                    基础校验通过
                  </>
                )}
              </span>
              <ChevronRight size={16} className="book-chevron" />
            </button>
          ))
        ) : (
          <EmptyState title={search || filtered ? '没有符合条件的企业' : '从第一家客户企业开始'}>
            {search || filtered
              ? '试试其他企业名称，或返回全部账套。'
              : '点击右上角“新建账套”，建立企业的独立工作空间。'}
          </EmptyState>
        )}
      </div>
      <PageControls label="企业账套" page={pagination} busy={busy || loading} onPage={onPage} />
      <div className="panel-footnote">
        <span className="small-dot" />
        仅展示获授权账套<span>异常数为基础校验汇总</span>
      </div>
    </section>
  );
}
