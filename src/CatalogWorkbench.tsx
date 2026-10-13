import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  CheckCircle2,
  FileJson,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { api } from './api';
import {
  auxiliaryLabels,
  businessTypeLabels,
  categoryLabels,
  chartImportText,
  parseAccountImport,
  parseTemplate,
  templateText,
} from './catalog-helpers';
import type {
  Catalog,
  CatalogImportPreview,
  CatalogTemplate,
  CatalogTemplatePreview,
} from './catalog-types';
import type { Session } from './types';
import './catalog.css';

const encoded = encodeURIComponent;
const failureMessage = (failure: unknown) =>
  failure instanceof Error ? failure.message : '操作未完成，请读取最新状态后重试。';

export function CatalogWorkbench({
  session,
  bookId,
  onBack,
  onBusyChange,
  onChanged,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (value: boolean) => void;
  onChanged?: () => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editor, setEditor] = useState<
    { kind: 'accounts' } | { kind: 'template'; template?: CatalogTemplate } | null
  >(null);
  const [text, setText] = useState('');
  const [importPreview, setImportPreview] = useState<CatalogImportPreview | null>(null);
  const [templatePreview, setTemplatePreview] = useState<CatalogTemplatePreview | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const locked = useRef(false);
  const target = `${session.scope_key}:${bookId}`;
  const targetRef = useRef(target);
  targetRef.current = target;
  const path = `/books/${encoded(bookId)}/catalog`;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      generation.current += 1;
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!bookId) {
        setLoading(false);
        return;
      }
      const epoch = ++generation.current;
      setLoading(true);
      try {
        const value = await api<Catalog>(`/books/${encoded(bookId)}/catalog`, session, { signal });
        if (!signal?.aborted && active.current && generation.current === epoch) {
          setCatalog(value);
          setError('');
        }
      } catch (failure) {
        if (!signal?.aborted && active.current && generation.current === epoch) {
          setCatalog(null);
          setError(failureMessage(failure));
        }
      } finally {
        if (!signal?.aborted && active.current && generation.current === epoch) setLoading(false);
      }
    },
    [bookId, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    setCatalog(null);
    setEditor(null);
    setImportPreview(null);
    setTemplatePreview(null);
    setError('');
    setNotice('');
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function openEditor(value: typeof editor, nextText: string) {
    setEditor(value);
    setText(nextText);
    setError('');
    setNotice('');
    setImportPreview(null);
    setTemplatePreview(null);
  }
  async function perform(save: boolean) {
    if (locked.current || !editor || !catalog?.can_manage) return;
    locked.current = true;
    setBusy(true);
    setError('');
    const commandTarget = target;
    const current = () => active.current && commandTarget === targetRef.current;
    try {
      if (editor.kind === 'accounts') {
        const rows = parseAccountImport(text);
        if (save) {
          if (!importPreview) throw new Error('请先预览整批科目。');
          await api(`${path}/accounts/import`, session, { method: 'POST', body: { rows } });
        } else {
          const result = await api<CatalogImportPreview>(
            `${path}/accounts/import/preview`,
            session,
            { method: 'POST', body: { rows } },
          );
          if (current()) setImportPreview(result);
        }
      } else {
        const definition = parseTemplate(text);
        if (save)
          await api(
            `${path}/templates${editor.template ? `/${encoded(editor.template.id)}` : ''}`,
            session,
            {
              method: editor.template ? 'PUT' : 'POST',
              body: {
                ...definition,
                ...(editor.template ? { expected_version: editor.template.version } : {}),
              },
            },
          );
        else {
          const example = {
            occurred_on: `${catalog.period}-01`,
            description: '模板演算示例',
            counterparty: '示例交易对方',
            amount: '113.00',
            direction: definition.direction,
            payment_status: definition.payment_status ?? 'paid',
            category: definition.category ?? 'office',
            project: '示例项目',
          };
          const result = await api<CatalogTemplatePreview>(`${path}/templates/preview`, session, {
            method: 'POST',
            body: {
              template: definition,
              payload: example,
              recognized_net: definition.lines.some((line) => line.amount_field !== 'gross')
                ? '100.00'
                : null,
              recognized_tax: definition.lines.some((line) => line.amount_field !== 'gross')
                ? '13.00'
                : null,
            },
          });
          if (current()) setTemplatePreview(result);
        }
      }
      if (save && current()) {
        setEditor(null);
        setImportPreview(null);
        setTemplatePreview(null);
        await load();
        if (current()) {
          setNotice('客户配置已保存，后续草稿使用最新版本；已有凭证仍由会计复核。');
          onChanged?.();
        }
      }
    } catch (failure) {
      if (current()) setError(failureMessage(failure));
    } finally {
      locked.current = false;
      if (current()) setBusy(false);
    }
  }
  return (
    <main className="catalog-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">A CHART THAT FITS THE BUSINESS</p>
          <h1>把企业的账，配置清楚。</h1>
          <p className="page-description">
            客户科目、辅助核算与固定记账模板，由授权会计维护并保留版本。
          </p>
        </div>
        <div className="catalog-heading-actions">
          <button className="button secondary" disabled={busy} onClick={onBack}>
            返回账套
          </button>
          <button
            className="button secondary"
            disabled={busy || loading || !bookId}
            onClick={() => void load()}
          >
            <RefreshCw size={15} />
            读取最新配置
          </button>
        </div>
      </section>
      <section className="catalog-scope">
        <label>
          <span>客户账套</span>
          <input
            aria-label="科目配置账套"
            readOnly
            value={catalog ? `${catalog.book_name} · ${catalog.period}` : '正在读取账套授权…'}
          />
        </label>
        <p>
          <ShieldCheck size={15} />
          科目由客户会计配置，系统不会自动认定行业准则或税务处理。
        </p>
      </section>
      {error && !editor && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {!bookId ? (
        <section className="panel empty-state">
          <BookOpen size={28} />
          <strong>当前没有获授权的账套</strong>
        </section>
      ) : loading ? (
        <div className="loading-block">
          <LoaderCircle size={17} className="spinner" />
          正在读取客户配置…
        </div>
      ) : (
        catalog && (
          <>
            <section className="panel catalog-chart">
              <div className="panel-heading">
                <div>
                  <h2>
                    企业科目<span className="count-label">{catalog.accounts.length}</span>
                  </h2>
                  <p>使用中的科目保留代码，可停用；新增辅助要求请建立明细科目。</p>
                </div>
                {catalog.can_manage && (
                  <button
                    className="button secondary small"
                    onClick={() =>
                      openEditor({ kind: 'accounts' }, chartImportText(catalog.accounts))
                    }
                  >
                    <FileJson size={14} />
                    导入 / 维护科目
                  </button>
                )}
              </div>
              <div className="catalog-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>科目代码</th>
                      <th>名称</th>
                      <th>类型 / 余额方向</th>
                      <th>必须填入的辅助项</th>
                      <th>状态 / 版本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalog.accounts.map((account) => (
                      <tr key={account.code}>
                        <td className="catalog-code">{account.code}</td>
                        <td>{account.name}</td>
                        <td>
                          {categoryLabels[account.category]} ·{' '}
                          {account.normal_balance === 'debit' ? '借方' : '贷方'}
                        </td>
                        <td>
                          {account.auxiliary_requirements
                            .map((key) => auxiliaryLabels[key])
                            .join('、') || '无'}
                        </td>
                        <td>
                          <span className={`badge ${account.active ? 'approved' : 'exception'}`}>
                            {account.active ? '启用' : '停用'}
                          </span>
                          <small>v{account.version}</small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel catalog-templates">
              <div className="panel-heading">
                <div>
                  <h2>
                    记账模板<span className="count-label">{catalog.templates.length}</span>
                  </h2>
                  <p>匹配已审批事项，按固定金额字段生成分录；会计复核后才能导出。</p>
                </div>
                {catalog.can_manage && (
                  <button
                    className="button primary small"
                    onClick={() => openEditor({ kind: 'template' }, templateText())}
                  >
                    <Plus size={14} />
                    配置模板
                  </button>
                )}
              </div>
              {catalog.templates.length ? (
                <div className="catalog-template-list">
                  {catalog.templates.map((template) => (
                    <article key={template.id}>
                      <div>
                        <strong>{template.name}</strong>
                        <span className={`badge ${template.active ? 'approved' : 'exception'}`}>
                          {template.active ? '启用' : '停用'} · v{template.version}
                        </span>
                      </div>
                      <p>
                        {businessTypeLabels[template.business_type]} ·{' '}
                        {template.direction === 'income' ? '收入' : '支出'} ·{' '}
                        {template.category ?? '全部费用类别'} ·{' '}
                        {template.payment_status === 'paid'
                          ? '已付款'
                          : template.payment_status === 'unpaid'
                            ? '未付款'
                            : '全部付款状态'}
                      </p>
                      <div className="catalog-formula">
                        {template.lines.map((line, index) => (
                          <span key={index}>
                            {line.side === 'debit' ? '借' : '贷'} {line.account_code}{' '}
                            <em>
                              {line.amount_field === 'gross'
                                ? '总额'
                                : line.amount_field === 'net'
                                  ? '不含税额'
                                  : '税额'}
                            </em>
                          </span>
                        ))}
                      </div>
                      {catalog.can_manage && (
                        <button
                          className="text-button"
                          onClick={() =>
                            openEditor({ kind: 'template', template }, templateText(template))
                          }
                        >
                          查看并修订模板
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="catalog-empty">
                  <FileJson size={23} />
                  <p>还没有客户记账模板</p>
                  <small>
                    先维护客户真实科目，再配置报销、采购 /
                    费用、日常收支模板。未匹配时保留现有人工流程。
                  </small>
                </div>
              )}
            </section>
          </>
        )
      )}
      {editor && (
        <CatalogEditor
          kind={editor.kind}
          text={text}
          busy={busy}
          error={error}
          importPreview={importPreview}
          templatePreview={templatePreview}
          onChange={(value) => {
            setText(value);
            setImportPreview(null);
            setTemplatePreview(null);
            setError('');
          }}
          onClose={() => {
            if (!busy) {
              setEditor(null);
              setError('');
            }
          }}
          onPreview={() => perform(false)}
          onSave={() => perform(true)}
        />
      )}
    </main>
  );
}

function CatalogEditor({
  kind,
  text,
  busy,
  error,
  importPreview,
  templatePreview,
  onChange,
  onClose,
  onPreview,
  onSave,
}: {
  kind: 'accounts' | 'template';
  text: string;
  busy: boolean;
  error: string;
  importPreview: CatalogImportPreview | null;
  templatePreview: CatalogTemplatePreview | null;
  onChange: (value: string) => void;
  onClose: () => void;
  onPreview: () => Promise<void>;
  onSave: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      className="modal catalog-modal"
      ref={dialog}
      aria-labelledby="catalog-editor-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="catalog-editor-title">
            {kind === 'accounts' ? '维护客户科目' : '配置固定记账模板'}
          </h2>
          <p>
            {kind === 'accounts'
              ? 'JSON 数组整批校验；未列出的科目保持现状。'
              : '只使用固定条件和 gross/net/tax 金额字段。'}
          </p>
        </div>
        <button className="icon-button" aria-label="关闭客户配置" disabled={busy} onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="form-content catalog-editor-content">
        <label>
          <span className="field-label">
            {kind === 'accounts' ? '科目配置 JSON' : '模板配置 JSON'}
          </span>
          <textarea
            aria-label={kind === 'accounts' ? '科目配置 JSON' : '模板配置 JSON'}
            rows={16}
            disabled={busy}
            value={text}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
          />
        </label>
        <p className="field-help">
          {kind === 'accounts'
            ? '新增科目省略 expected_version；更新科目保留最新版本。active:false 停用；代码不可改。辅助项支持 department、counterparty、employee、project。'
            : '模板条件：business_type、direction，可加 category、payment_status。示例演算使用总额 113、不含税额 100、税额 13，仅验证公式，不构成客户业务的税额判断。'}
        </p>
        {error && (
          <p className="form-error" role="alert">
            <TriangleAlert size={15} />
            {error}
          </p>
        )}
        {importPreview && (
          <p className="catalog-preview-note">
            <CheckCircle2 size={16} />
            整批有效：新增 {importPreview.create_count} 项，更新 {importPreview.update_count}{' '}
            项。保存时再次核对权限、期间与版本。
          </p>
        )}
        {templatePreview && (
          <div className="catalog-preview">
            <strong>固定金额演算</strong>
            {templatePreview.lines.map((line, index) => (
              <p key={index}>
                <span>{line.account_code}</span>
                <span>
                  借 {line.debit} / 贷 {line.credit}
                </span>
              </p>
            ))}
            {templatePreview.issues.map((issue, index) => (
              <p className="field-help" key={index}>
                {issue}
              </p>
            ))}
          </div>
        )}
      </div>
      <div className="modal-footer">
        <button className="button secondary" disabled={busy} onClick={onClose}>
          取消
        </button>
        <button
          className="button secondary"
          disabled={busy || !text.trim()}
          onClick={() => void onPreview()}
        >
          {busy ? <LoaderCircle size={15} className="spinner" /> : <FileJson size={15} />}
          {kind === 'accounts' ? '预览整批科目' : '演算示例'}
        </button>
        <button
          className="button primary"
          disabled={busy || !text.trim() || (kind === 'accounts' && !importPreview)}
          onClick={() => void onSave()}
        >
          <CheckCircle2 size={15} />
          保存配置
        </button>
      </div>
    </dialog>
  );
}
