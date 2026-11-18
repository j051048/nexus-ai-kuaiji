import { useCallback, useEffect, useState } from 'react';
import { Link2, RefreshCw } from 'lucide-react';
import { api } from './api';
import type { MasterEntity } from './ledger-types';
import type { Session } from './types';

const enc = encodeURIComponent;
const errorText = (failure: unknown) =>
  failure instanceof Error ? failure.message : '主体别名未读取。';
type AliasRow = {
  id: string;
  subject_type: string;
  subject_id: string;
  subject_name: string | null;
  alias: string;
  source: string;
  status: string;
  effective_from: string;
  effective_to: string | null;
};

export function SubjectAliasPanel({
  session,
  companyId,
  entities,
  canManage,
}: {
  session: Session;
  companyId: string;
  entities: MasterEntity[];
  canManage: boolean;
}) {
  const [items, setItems] = useState<AliasRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [alias, setAlias] = useState('');
  const [source, setSource] = useState('历史台账');
  const [effectiveFrom, setEffectiveFrom] = useState('2026-10-01');
  const [retireReason, setRetireReason] = useState('');
  const path = `/companies/${enc(companyId)}/aliases?subject_type=counterparty`;
  const counterparties = entities.filter((item) => item.kind === 'counterparty' && item.active);

  const load = useCallback(async () => {
    const result = await api<{ items: AliasRow[] }>(path, session);
    setItems(result.items);
    setError('');
  }, [path, session]);

  useEffect(() => {
    setLoading(true);
    void load()
      .catch((failure) => setError(errorText(failure)))
      .finally(() => setLoading(false));
  }, [load]);

  async function run(action: () => Promise<string>) {
    setError('');
    setNotice('');
    try {
      setNotice(await action());
      await load();
    } catch (failure) {
      setError(errorText(failure));
    }
  }

  return (
    <section className="panel subject-alias-panel" aria-label="客商通用别名">
      <div className="panel-heading">
        <div>
          <h2>
            <Link2 size={18} />
            客商通用别名
          </h2>
          <p>
            用于历史名称、简称与稳定主数据之间的唯一匹配；同一有效期不能指向两个客商。停用只关闭后续解析，不改历史凭证。
          </p>
        </div>
        <button className="button secondary small" onClick={() => void load()}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="form-notice">{notice}</div>}
      {loading && <p className="field-help">正在读取客商别名…</p>}
      {!loading && (
        <>
          {items.length === 0 ? (
            <p className="field-help">当前企业尚未登记客商别名。</p>
          ) : (
            <div className="ledger-alias-list">
              {items.map((item) => (
                <article key={item.id}>
                  <div>
                    <strong>{item.alias}</strong>
                    <span>→ {item.subject_name || item.subject_id}</span>
                    <small>
                      {item.status === 'approved' ? '生效' : '已停用'} ·{' '}
                      {item.effective_from}
                      {item.effective_to ? ` 至 ${item.effective_to}` : ' 起'}
                    </small>
                  </div>
                  <p>{item.source}</p>
                  {canManage && item.status === 'approved' && (
                    <button
                      className="button secondary small"
                      disabled={retireReason.trim().length < 5}
                      onClick={() =>
                        void run(async () => {
                          await api(
                            `/companies/${enc(companyId)}/aliases/${enc(item.id)}/retire`,
                            session,
                            { method: 'POST', body: { reason: retireReason } },
                          );
                          setRetireReason('');
                          return '别名已停用；历史凭证仍保留原名称。';
                        })
                      }
                    >
                      停用别名
                    </button>
                  )}
                </article>
              ))}
            </div>
          )}
          {canManage && (
            <form
              className="ledger-alias-form"
              onSubmit={(event) => {
                event.preventDefault();
                void run(async () => {
                  await api(`/companies/${enc(companyId)}/aliases`, session, {
                    method: 'POST',
                    body: {
                      subject_type: 'counterparty',
                      subject_id: subjectId,
                      alias,
                      source,
                      effective_from: effectiveFrom,
                    },
                  });
                  setAlias('');
                  return '客商别名已登记；解析使用生效期内的唯一匹配。';
                });
              }}
            >
              <label>
                对应客商主数据
                <select
                  required
                  value={subjectId}
                  onChange={(event) => setSubjectId(event.target.value)}
                >
                  <option value="">选择客商</option>
                  {counterparties.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · {item.code}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                历史名称 / 简称
                <input required value={alias} onChange={(event) => setAlias(event.target.value)} />
              </label>
              <label>
                来源
                <input required value={source} onChange={(event) => setSource(event.target.value)} />
              </label>
              <label>
                生效日
                <input
                  type="date"
                  required
                  value={effectiveFrom}
                  onChange={(event) => setEffectiveFrom(event.target.value)}
                />
              </label>
              <label>
                停用原因（至少 5 字，用于停用按钮）
                <input
                  value={retireReason}
                  onChange={(event) => setRetireReason(event.target.value)}
                />
              </label>
              <button
                className="button secondary"
                disabled={!subjectId || !alias.trim() || !source.trim()}
              >
                登记别名
              </button>
            </form>
          )}
          {!canManage && <p className="field-help">当前账号只读，不能登记或停用别名。</p>}
        </>
      )}
    </section>
  );
}
