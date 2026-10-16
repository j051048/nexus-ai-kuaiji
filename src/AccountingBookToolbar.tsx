import { Settings2, Upload } from 'lucide-react';
import type { Book } from './types';
import type { WorkspaceView } from './workspace-route';

type BookAction = {
  view: WorkspaceView;
  label: string;
  reviewer?: boolean;
  protectChanges?: boolean;
};

export type BookPipelineStep = 'intake' | 'matching' | 'queue' | 'closing' | 'delivery';

const pipelineSteps: { id: BookPipelineStep; label: string; view: WorkspaceView }[] = [
  { id: 'intake', label: '① 资料与流水', view: 'accounting' },
  { id: 'matching', label: '② 匹配与核销', view: 'reconciliation' },
  { id: 'queue', label: '③ 凭证队列与复核', view: 'accounting' },
  { id: 'closing', label: '④ 对账与月结', view: 'balances' },
  { id: 'delivery', label: '⑤ 交付与导出', view: 'delivery' },
];

/** Which stage of the book pipeline a view belongs to; null for settings-only screens. */
export function bookPipelineStep(
  view: WorkspaceView,
  listTab: 'drafts' | 'sources' | 'exports',
): BookPipelineStep | null {
  if (view === 'accounting')
    return listTab === 'sources' ? 'intake' : listTab === 'exports' ? 'delivery' : 'queue';
  if (view === 'recurring' || view === 'materials') return 'intake';
  if (view === 'reconciliation' || view === 'settlement') return 'matching';
  if (view === 'balances' || view === 'closing') return 'closing';
  if (view === 'delivery' || view === 'integration') return 'delivery';
  if (view === 'briefing') return 'delivery';
  return null;
}

const pipelineGroups: { label: string; actions: BookAction[] }[] = [
  {
    label: '① 资料与流水',
    actions: [
      { view: 'recurring', label: '固定事项草稿', protectChanges: true },
      { view: 'materials', label: '月度资料清单', protectChanges: true },
    ],
  },
  {
    label: '② 匹配与核销',
    actions: [{ view: 'settlement', label: '收付款核销', reviewer: true, protectChanges: true }],
  },
  {
    label: '④ 对账与月结',
    actions: [
      { view: 'balances', label: '参考余额与月度差异', protectChanges: true },
      { view: 'closing', label: '月结预检', reviewer: true, protectChanges: true },
    ],
  },
  {
    label: '⑤ 交付与导出',
    actions: [
      { view: 'briefing', label: '月度财税简报', protectChanges: true },
      { view: 'integration', label: '财务软件接入', protectChanges: true },
    ],
  },
];

const settingActions: BookAction[] = [
  { view: 'catalog', label: '科目与模板', protectChanges: true },
  { view: 'learning', label: '审核样例与规则', reviewer: true, protectChanges: true },
  { view: 'ledger', label: '长期账套与主数据', protectChanges: true },
  { view: 'migration', label: '旧账迁移', protectChanges: true },
];

export function AccountingBookToolbar({
  book,
  fallbackName,
  busy,
  loading,
  dirty,
  activeStep,
  onImport,
  onWorkspace,
}: {
  book?: Book;
  fallbackName?: string;
  busy: boolean;
  loading: boolean;
  dirty: boolean;
  activeStep?: BookPipelineStep | null;
  onImport: () => void;
  onWorkspace: (view: WorkspaceView) => void;
}) {
  const blocked = (action: BookAction) =>
    !book ||
    busy ||
    loading ||
    (!!action.protectChanges && dirty) ||
    (!!action.reviewer && !book.permissions.review);
  const actionButton = (action: BookAction) => (
    <button
      key={action.label}
      type="button"
      className="button secondary small"
      disabled={blocked(action)}
      onClick={() => onWorkspace(action.view)}
    >
      {action.label}
    </button>
  );
  return (
    <div className="panel-heading transaction-heading">
      <div>
        <div className="section-eyebrow">当前账套</div>
        <h2 className="selected-book-title">{book?.name ?? fallbackName ?? '尚未选择账套'}</h2>
        <nav className="book-steps" aria-label="做账五步流水线">
          {pipelineSteps.map((step) => (
            <button
              key={step.id}
              type="button"
              className={'book-step' + (activeStep === step.id ? ' active' : '')}
              disabled={!book || busy || loading || dirty}
              aria-current={activeStep === step.id ? 'step' : undefined}
              onClick={() => onWorkspace(step.view)}
            >
              {step.label}
            </button>
          ))}
        </nav>
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
            {pipelineGroups.map((group) => (
              <div className="book-more-group" key={group.label}>
                <span className="book-more-group-label">{group.label}</span>
                {group.actions.map(actionButton)}
              </div>
            ))}
          </div>
        </details>
        <details className="book-settings">
          <summary className="button secondary small">
            <Settings2 size={14} />
            账套设置
          </summary>
          <div className="book-more-menu">{settingActions.map(actionButton)}</div>
        </details>
      </div>
    </div>
  );
}
