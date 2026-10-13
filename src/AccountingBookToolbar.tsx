import { Upload } from 'lucide-react';
import type { Book } from './types';
import type { WorkspaceView } from './workspace-route';

const bookActions: {
  view: WorkspaceView;
  label: string;
  reviewer?: boolean;
  protectChanges?: boolean;
}[] = [
  { view: 'migration', label: '旧账迁移', protectChanges: true },
  { view: 'settlement', label: '收付款核销', reviewer: true, protectChanges: true },
  { view: 'learning', label: '审核样例与规则', reviewer: true, protectChanges: true },
  { view: 'catalog', label: '科目与模板', protectChanges: true },
  { view: 'ledger', label: '长期账套与主数据', protectChanges: true },
  { view: 'integration', label: '财务软件接入', protectChanges: true },
  { view: 'efficiency', label: '人工效率', protectChanges: true },
  { view: 'quality', label: '试点评测', reviewer: true, protectChanges: true },
  { view: 'closing', label: '月结预检', reviewer: true, protectChanges: true },
];
export function AccountingBookToolbar({
  book,
  fallbackName,
  busy,
  loading,
  dirty,
  onImport,
  onWorkspace,
}: {
  book?: Book;
  fallbackName?: string;
  busy: boolean;
  loading: boolean;
  dirty: boolean;
  onImport: () => void;
  onWorkspace: (view: WorkspaceView) => void;
}) {
  return (
    <div className="panel-heading transaction-heading">
      <div>
        <div className="section-eyebrow">当前账套</div>
        <h2 className="selected-book-title">{book?.name ?? fallbackName ?? '尚未选择账套'}</h2>
      </div>
      <div className="toolbar-primary">
        <button
          className="button secondary small"
          disabled={!book?.permissions.edit || busy || loading || dirty}
          onClick={onImport}
        >
          <Upload size={14} />
          导入流水
        </button>
        <button
          type="button"
          className="button secondary small"
          disabled={!book?.permissions.edit || busy || loading || dirty}
          onClick={() => onWorkspace('reconciliation')}
        >
          流水与业务匹配
        </button>
        <button
          type="button"
          className="button secondary small"
          disabled={!book || busy || loading || dirty}
          onClick={() => onWorkspace('delivery')}
        >
          导入回执核验
        </button>
        <details className="book-more-actions">
          <summary className="button secondary small">更多账套操作</summary>
          <div className="book-more-menu">
            {bookActions.map((action) => (
              <button
                key={action.view}
                type="button"
                className="button secondary small"
                disabled={
                  !book ||
                  busy ||
                  loading ||
                  (!!action.protectChanges && dirty) ||
                  (!!action.reviewer && !book.permissions.review)
                }
                onClick={() => onWorkspace(action.view)}
              >
                {action.label}
              </button>
            ))}
          </div>
        </details>
      </div>
    </div>
  );
}
