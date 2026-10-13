import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  CheckCircle2,
  FileCheck2,
  RefreshCw,
  Split,
  TriangleAlert,
  Undo2,
} from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import { cents, decimal, selectableItems } from './settlement-helpers';
import type {
  SettlementAllocation,
  SettlementBank,
  SettlementCandidate,
  SettlementOverview,
  SettlementPreview,
} from './settlement-helpers';
import './settlements.css';
const encoded = encodeURIComponent;
const message = (error: unknown) =>
  error instanceof Error ? error.message : '核销操作未确认，请读取最新回执。';
type Clearance = {
  draft_id: string;
  draft_version: number;
  description: string;
  lines: {
    account_code: string;
    debit: string;
    credit: string;
    auxiliary_values: Record<string, string>;
  }[];
  issues: string[];
};

export function SettlementWorkbench({
  session,
  bookId,
  onBack,
  onBusyChange,
  onChanged,
  onOpenDraft,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => void;
  onOpenDraft?: (draftId: string) => void;
}) {
  const [overview, setOverview] = useState<SettlementOverview | null>(null),
    [offset, setOffset] = useState(0),
    [bankId, setBankId] = useState('');
  const [amounts, setAmounts] = useState<Record<string, string>>({}),
    [reason, setReason] = useState(''),
    [preview, setPreview] = useState<SettlementPreview | null>(null),
    [clearance, setClearance] = useState<Clearance | null>(null),
    [reversal, setReversal] = useState<SettlementAllocation | null>(null);
  const [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const scope = `${session.scope_key}:${bookId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const epoch = useRef(0),
    commandEpoch = useRef(0),
    active = useRef(true),
    locked = useRef(false);
  const path = `/books/${encoded(bookId)}/settlements`,
    bank = overview?.banks.find((item) => item.id === bankId) ?? null;
  const candidates = selectableItems(overview?.items ?? [], bank);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
    };
  }, [scope]);
  useEffect(() => {
    onBusyChange?.(busy || !!preview || !!clearance || !!reversal);
    return () => onBusyChange?.(false);
  }, [busy, preview, clearance, reversal, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const captured = scope,
        stamp = ++epoch.current;
      setLoading(true);
      try {
        const value = await api<SettlementOverview>(
          `/books/${encoded(bookId)}/settlements?limit=20&offset=${offset}`,
          session,
          { signal },
        );
        if (
          active.current &&
          !signal?.aborted &&
          captured === scopeRef.current &&
          stamp === epoch.current
        ) {
          setOverview(value);
          setError('');
        }
      } catch (failure) {
        if (
          active.current &&
          !signal?.aborted &&
          captured === scopeRef.current &&
          stamp === epoch.current
        ) {
          setOverview(null);
          setError(message(failure));
        }
      } finally {
        if (active.current && captured === scopeRef.current && stamp === epoch.current)
          setLoading(false);
      }
    },
    [bookId, offset, scope, session],
  );
  useEffect(() => {
    epoch.current += 1;
    commandEpoch.current += 1;
    locked.current = false;
    setBusy(false);
    setOverview(null);
    setOffset(0);
    setBankId('');
    setAmounts({});
    setPreview(null);
    setClearance(null);
    setReversal(null);
    setReason('');
    setNotice('');
    setError('');
  }, [scope]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    const captured = scope,
      stamp = ++commandEpoch.current;
    locked.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      if (active.current && captured === scopeRef.current && stamp === commandEpoch.current)
        setError(message(failure));
    } finally {
      if (stamp === commandEpoch.current) locked.current = false;
      if (active.current && captured === scopeRef.current && stamp === commandEpoch.current)
        setBusy(false);
    }
  }
  function current(captured: string) {
    return active.current && captured === scopeRef.current;
  }
  async function openItem(candidate: SettlementCandidate) {
    const captured = scope;
    await run(async () => {
      await api(`${path}/items`, session, {
        method: 'POST',
        body: {
          source_id: candidate.source_id,
          counterparty_id: candidate.counterparty_id,
          expected_draft_version: candidate.draft_version,
          idempotency_key: crypto.randomUUID(),
          reason: reason.trim(),
        },
      });
      if (!current(captured)) return;
      setNotice('已登记辅助未结事项。原业务事实及分录保持原审核流程。');
      await load();
      onChanged?.();
    });
  }
  async function makePreview() {
    if (!bank) return;
    const captured = scope;
    await run(async () => {
      const value = await api<SettlementPreview>(`${path}/previews`, session, {
        method: 'POST',
        body: {
          bank_source_id: bank.id,
          parts: Object.entries(amounts)
            .filter(([, value]) => value && cents(value) > 0)
            .map(([item_id, amount]) => ({ item_id, amount })),
          reason: reason.trim(),
        },
      });
      if (current(captured)) setPreview(value);
    });
  }
  async function confirmPreview() {
    if (!preview) return;
    const captured = scope,
      p = preview;
    await run(async () => {
      await api(`${path}/previews/${encoded(p.id)}/confirm`, session, {
        method: 'POST',
        body: { idempotency_key: crypto.randomUUID() },
      });
      if (!current(captured)) return;
      setPreview(null);
      setAmounts({});
      setNotice('分配已持久记录。先复核原应收/应付凭证，再生成并复核银行清偿草稿。');
      await load();
      onChanged?.();
    });
  }
  async function split() {
    if (!bank) return;
    const captured = scope;
    await run(async () => {
      const selected = candidates.filter((item) => item.id in amounts);
      if (!selected.length) throw new Error('请先勾选待分摊事项');
      const value = await api<{ parts: { key: string; amount: string }[] }>(
        `${path}/split`,
        session,
        {
          method: 'POST',
          body: {
            total: bank.remaining,
            parts: selected.map((item) => ({ key: item.id, weight: cents(item.remaining) })),
          },
        },
      );
      if (current(captured))
        setAmounts(Object.fromEntries(value.parts.map((item) => [item.key, item.amount])));
    });
  }
  async function inspectClearance(item: SettlementBank) {
    const captured = scope;
    await run(async () => {
      const value = await api<Clearance>(`${path}/banks/${encoded(item.id)}/clearance`, session);
      if (current(captured)) setClearance(value);
    });
  }
  async function applyClearance() {
    if (!clearance) return;
    const captured = scope,
      c = clearance;
    await run(async () => {
      await api(`/books/${encoded(bookId)}/drafts/${encoded(c.draft_id)}`, session, {
        method: 'PATCH',
        body: {
          expected_version: c.draft_version,
          description: c.description,
          lines: c.lines,
          correction_reason: '按已确认辅助核销生成应收/应付清偿分录，最终仍需会计复核',
        },
      });
      if (!current(captured)) return;
      setClearance(null);
      setNotice('银行清偿草稿已生成，尚未审核或导出。辅助项可在凭证复核页补齐。');
      await load();
      onChanged?.();
    });
  }
  async function reverse() {
    if (!reversal) return;
    const captured = scope,
      row = reversal;
    await run(async () => {
      await api(`${path}/allocations/${encoded(row.id)}/reverse`, session, {
        method: 'POST',
        body: {
          expected_fingerprint: row.fingerprint,
          idempotency_key: crypto.randomUUID(),
          reason: reason.trim(),
        },
      });
      if (!current(captured)) return;
      setReversal(null);
      setNotice('已追加独立撤销记录，原分配保留。银行清偿草稿需要重新核对。');
      await load();
      onChanged?.();
    });
  }
  const modalOpen = !!preview || !!clearance || !!reversal,
    blocked = busy || loading || modalOpen || overview?.book.period_status !== 'open';
  return (
    <main className="settlement-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">RECEIVABLE / PAYABLE CLEARANCE</p>
          <h1>一笔付款，可以对应多项业务。</h1>
          <p className="page-description">
            登记未结业务、分配实际收付，再按应收/应付生成银行清偿草稿。原件与历史逐笔保留。
          </p>
        </div>
        <div className="settlement-actions">
          <button className="button secondary" disabled={busy || modalOpen} onClick={onBack}>
            <ArrowLeft size={15} />
            返回账套
          </button>
          <button
            className="button secondary"
            disabled={busy || modalOpen || loading}
            onClick={() => void load()}
          >
            <RefreshCw size={15} />
            读取最新余额
          </button>
        </div>
      </section>
      {error && (
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
      <div className="settlement-scope">
        当前范围：{overview?.book.period ?? '读取中'} · 人民币 ·
        同一开放期间辅助核销。跨期结算、复杂往来及已导出调整须另行处理。
      </div>
      <section className="settlement-registration">
        <h2>登记未结事项</h2>
        <label>
          核对依据
          <input
            aria-label="核销核对依据"
            value={reason}
            maxLength={1000}
            disabled={busy || !!preview || !!clearance}
            onChange={(event) => setReason(event.target.value)}
            placeholder="核对合同、未付款业务与收付回单的依据"
          />
        </label>
        {overview?.candidates.map((item) => (
          <div className="settlement-candidate" key={item.source_id}>
            <div>
              <strong>{item.description}</strong>
              <p>
                {item.counterparty} · {item.amount} 元 · 应收/应付科目 {item.clearing_account}
              </p>
              {!item.counterparty_id && <small>请先在主数据工作台建立或审核交易对方映射</small>}
            </div>
            <button
              className="button secondary"
              disabled={blocked || !item.counterparty_id || reason.trim().length < 3}
              onClick={() => void openItem(item)}
            >
              登记未结事项
            </button>
          </div>
        ))}
        {overview && !overview.candidates.length && (
          <p className="settlement-muted">暂无可登记的当前未审核、未收付款业务。</p>
        )}
      </section>
      <section className="settlement-allocation">
        <div>
          <h2>分配银行收付</h2>
          <p>金额分别不能超过业务未结余额和银行未分配余额。</p>
        </div>
        <label>
          选择银行流水
          <select
            aria-label="核销银行流水"
            disabled={blocked}
            value={bankId}
            onChange={(event) => {
              setBankId(event.target.value);
              setAmounts({});
            }}
          >
            <option value="">请选择实际收付回单</option>
            {overview?.banks.map((item) => (
              <option key={item.id} value={item.id}>
                {item.date} {item.counterparty} {item.amount}元（剩余{item.remaining}）
              </option>
            ))}
          </select>
        </label>
        {bank && (
          <div className="settlement-bank-fact">
            <strong>{bank.description}</strong>
            <span>
              {bank.counterparty} · {bank.direction === 'expense' ? '付款' : '收款'} · 尚可分配{' '}
              {bank.remaining} 元
            </span>
            {!bank.counterparty_id && <small>交易对方未映射，暂不可分配。</small>}
          </div>
        )}
        <div className="settlement-table">
          <table>
            <thead>
              <tr>
                <th>选择</th>
                <th>原业务科目</th>
                <th>业务金额</th>
                <th>未结余额</th>
                <th>本次分配</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((item) => (
                <tr key={item.id}>
                  <td>
                    <input
                      aria-label={`选择核销事项${item.id}`}
                      type="checkbox"
                      disabled={blocked}
                      checked={item.id in amounts}
                      onChange={(event) =>
                        setAmounts((old) => {
                          const next = { ...old };
                          if (event.target.checked) next[item.id] = item.remaining;
                          else delete next[item.id];
                          return next;
                        })
                      }
                    />
                  </td>
                  <td>
                    {item.clearing_account} · {item.counterparty_original}
                  </td>
                  <td>{item.amount}</td>
                  <td>{item.remaining}</td>
                  <td>
                    <input
                      aria-label={`分配金额${item.id}`}
                      inputMode="decimal"
                      value={amounts[item.id] ?? ''}
                      disabled={blocked || !(item.id in amounts)}
                      onChange={(event) =>
                        setAmounts((old) => ({ ...old, [item.id]: event.target.value }))
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="settlement-actions">
          <button
            className="button secondary"
            disabled={blocked || !bank || !Object.keys(amounts).length}
            onClick={() => void split()}
          >
            <Split size={15} />
            按所选余额比例分摊
          </button>
          <button
            className="button primary"
            disabled={blocked || !bank || !Object.keys(amounts).length || reason.trim().length < 3}
            onClick={() => void makePreview()}
          >
            <FileCheck2 size={15} />
            预览收付分配
          </button>
        </div>
      </section>
      <section className="settlement-history">
        <h2>未结余额</h2>
        <div className="settlement-table">
          <table>
            <thead>
              <tr>
                <th>交易对方</th>
                <th>原始金额</th>
                <th>已分配</th>
                <th>未结余额</th>
                <th>原凭证</th>
              </tr>
            </thead>
            <tbody>
              {overview?.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.counterparty_original}</td>
                  <td>{item.amount}</td>
                  <td>{item.allocated}</td>
                  <td>{item.remaining}</td>
                  <td>
                    {item.is_current === false
                      ? '历史事项，当前不可分配'
                      : item.draft_status === 'draft'
                        ? '待审核'
                        : '已审核 / 已导出'}
                    {onOpenDraft && (
                      <button
                        className="button secondary small"
                        disabled={blocked}
                        onClick={() => onOpenDraft(item.draft_id)}
                      >
                        打开原业务凭证
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="settlement-actions">
          <button
            className="button secondary"
            disabled={blocked || !offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </button>
          <span>共 {overview?.total ?? 0} 项</span>
          <button
            className="button secondary"
            disabled={blocked || !overview?.has_more}
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </button>
        </div>
      </section>
      <section className="settlement-history">
        <h2>收付分配历史与清偿</h2>
        {overview?.allocations.map((row) => (
          <div className="settlement-history-row" key={row.id}>
            <div>
              <strong>
                {row.amount} 元 · {row.clearing_account}
              </strong>
              <p>
                {row.reversed ? '已独立撤销，历史保留' : '当前有效分配'} ·{' '}
                {new Date(row.created_at).toLocaleString('zh-CN')}
              </p>
            </div>
            <div className="settlement-actions">
              {!row.reversed && overview.banks.find((item) => item.id === row.bank_source_id) && (
                <button
                  className="button secondary"
                  disabled={blocked}
                  onClick={() =>
                    void inspectClearance(
                      overview.banks.find((item) => item.id === row.bank_source_id)!,
                    )
                  }
                >
                  检查 / 生成清偿草稿
                </button>
              )}
              <button
                className="button secondary"
                disabled={blocked || !row.can_reverse}
                onClick={() => {
                  setReversal(row);
                  setReason('');
                }}
              >
                <Undo2 size={14} />
                独立撤销
              </button>
            </div>
          </div>
        ))}
      </section>
      {preview && (
        <div className="modal-backdrop">
          <section
            className="settlement-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="收付分配预览"
          >
            <h2>确认辅助核销分配</h2>
            <p>
              本次合计 {decimal(preview.plan.total_cents)}{' '}
              元。这里只登记关联，银行清偿凭证仍需另行会计复核。
            </p>
            {preview.plan.parts.map((part) => (
              <div key={part.item_id} className="settlement-preview-line">
                <span>科目 {part.clearing_account}</span>
                <strong>{decimal(part.amount_cents)} 元</strong>
              </div>
            ))}
            <div className="settlement-actions">
              <button className="button secondary" disabled={busy} onClick={() => setPreview(null)}>
                取消
              </button>
              <button
                className="button primary"
                disabled={busy}
                onClick={() => void confirmPreview()}
              >
                <CheckCircle2 size={15} />
                确认分配
              </button>
            </div>
          </section>
        </div>
      )}
      {clearance && (
        <div className="modal-backdrop">
          <section
            className="settlement-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="银行清偿草稿"
          >
            <h2>银行清偿草稿</h2>
            {clearance.issues.map((issue) => (
              <p className="inline-error" key={issue}>
                {issue}
              </p>
            ))}
            {clearance.lines.map((line, index) => (
              <div className="settlement-preview-line" key={index}>
                <span>{line.account_code}</span>
                <strong>
                  借 {line.debit} / 贷 {line.credit}
                </strong>
              </div>
            ))}
            <p>生成后请到凭证复核页补齐辅助项并审核。不会自动过账或导出。</p>
            <div className="settlement-actions">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setClearance(null)}
              >
                关闭
              </button>
              {onOpenDraft && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => {
                    const id = clearance.draft_id;
                    setClearance(null);
                    onOpenDraft(id);
                  }}
                >
                  打开凭证复核
                </button>
              )}
              <button
                className="button primary"
                disabled={busy || !!clearance.issues.length}
                onClick={() => void applyClearance()}
              >
                生成未审核清偿草稿
              </button>
            </div>
          </section>
        </div>
      )}
      {reversal && (
        <div className="modal-backdrop">
          <section
            className="settlement-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="独立撤销核销分配"
          >
            <h2>独立撤销 {reversal.amount} 元分配</h2>
            <p>原分配保留，恢复业务和银行余额；已审核或导出的银行分配不能撤销。</p>
            <label>
              独立核实依据
              <textarea
                value={reason}
                disabled={busy}
                maxLength={1000}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <div className="settlement-actions">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setReversal(null)}
              >
                取消
              </button>
              <button
                className="button primary"
                disabled={busy || reason.trim().length < 3}
                onClick={() => void reverse()}
              >
                追加撤销记录
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
