import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { useScopedQuery } from './useScopedQuery';
import {
  agingBuckets,
  agingKinds,
  parseAging,
  parsePeriodic,
  type PeriodicWorkspace,
} from './finance-operations';
import './finance-operations.css';
import { SettlementDetail } from './SettlementDetail';
import { AssetReconciliation } from './AssetReconciliation';

type Props = {
  session: Session;
  bookId: string;
  companyId: string;
  period: string;
  initialTab?: 'aging' | 'periodic';
  onChanged?: () => void;
  onVoucher?: (id: string) => void;
};
const amount = (cents: number) =>
  `${Math.trunc(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
const monthEnd = (period: string) => {
  const [year, month] = period.split('-').map(Number);
  return `${period}-${new Date(Date.UTC(year, month, 0)).getUTCDate()}`;
};
export function FinanceOperationsPanel(props: Props) {
  return (
    <Content
      key={`${props.session.scope_key}:${props.bookId}:${props.companyId}:${props.period}`}
      {...props}
    />
  );
}
function Content(props: Props) {
  const [tab, setTab] = useState<'aging' | 'periodic'>(props.initialTab ?? 'aging');
  const [revoked, setRevoked] = useState(false);
  useEffect(() => {
    const deny = (event: Event) => {
      if ((event as CustomEvent<string>).detail === props.session.scope_key) setRevoked(true);
    };
    window.addEventListener('accounting:forbidden', deny);
    window.addEventListener('accounting:unauthorized', deny);
    return () => {
      window.removeEventListener('accounting:forbidden', deny);
      window.removeEventListener('accounting:unauthorized', deny);
    };
  }, [props.session.scope_key]);
  if (revoked) return <p role="alert">授权已变更，已清空数据。请确认权限后重新打开本账套。</p>;
  return (
    <section aria-label="往来与期末办理" className="finance-operations">
      <nav className="formal-books-tabs" aria-label="往来与期末办理类型">
        <button
          type="button"
          className="button secondary"
          aria-pressed={tab === 'aging'}
          onClick={() => setTab('aging')}
        >
          往来账龄
        </button>
        <button
          type="button"
          className="button secondary"
          aria-pressed={tab === 'periodic'}
          onClick={() => setTab('periodic')}
        >
          资产与期末草稿
        </button>
      </nav>
      {tab === 'aging' ? <Aging {...props} /> : <Periodic {...props} />}
    </section>
  );
}
function Aging({ session, bookId, companyId, period, onChanged, onVoucher }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const returnFocus = useRef<string | null>(null);
  const [date, setDate] = useState(monthEnd(period)),
    [kind, setKind] = useState<keyof typeof agingKinds>('receivable');
  const expected = { firmId: session.firm_id, bookId, companyId, period };
  const query = useScopedQuery(
    async (signal) => ({
      date,
      value: parseAging(
        await api<unknown>(
          `/subledger/aging?book_id=${encodeURIComponent(bookId)}&as_of=${encodeURIComponent(date)}`,
          session,
          { signal },
        ),
        expected,
        date,
      ),
    }),
    date,
  );
  const data = query.data?.date === date ? query.data.value : null;
  const rows = data?.items.filter((row) => row.kind === kind) ?? [];
  useEffect(() => {
    if (!selected && !query.loading && data && returnFocus.current) {
      document.getElementById(`settlement-${returnFocus.current}`)?.focus();
      returnFocus.current = null;
    }
  }, [selected, query.loading, data]);
  if (selected)
    return (
      <SettlementDetail
        key={selected}
        session={session}
        bookId={bookId}
        companyId={companyId}
        period={period}
        itemId={selected}
        onVoucher={onVoucher}
        onChanged={() => {
          query.reload();
          onChanged?.();
        }}
        onBack={() => {
          returnFocus.current = selected;
          setSelected(null);
          query.reload();
        }}
      />
    );
  return (
    <>
      <p>按未结事项及已记账核销查看，不把应收、应付与预收预付相互抵消。此处不会新增收付款凭证。</p>
      <div className="formal-books-controls">
        <label>
          账龄截止日期
          <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        </label>
        <label>
          往来类型
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as keyof typeof agingKinds)}
          >
            {Object.entries(agingKinds).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button secondary"
          onClick={query.reload}
          disabled={query.loading}
        >
          重新读取
        </button>
      </div>
      {query.loading && <p role="status">正在读取账龄…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {data && (
        <>
          <dl className="finance-aging-summary">
            {Object.entries(agingBuckets).map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{amount(data.by_kind[kind][key as keyof typeof agingBuckets])} 元</dd>
              </div>
            ))}
          </dl>
          {!rows.length && (
            <p>
              截至所选日期，没有未结的{agingKinds[kind]}
              。如应有数据，请由本账套会计核对已登记的往来事项及核销记录。
            </p>
          )}
          {rows.map((row) => (
            <article className="review-section" key={row.id}>
              <h3>{row.counterparty}</h3>
              <p>
                未结金额 {amount(row.remaining_cents)} 元 · {agingBuckets[row.bucket]}
              </p>
              <p>
                发生日期 {row.original_date} · 到期日期{' '}
                {row.due_date ?? '未登记（以发生日期计算账龄）'}
              </p>
              <button
                id={`settlement-${row.id}`}
                type="button"
                className="button secondary"
                onClick={() => setSelected(row.id)}
              >
                查看并办理核销
              </button>
            </article>
          ))}
        </>
      )}
    </>
  );
}
function Periodic({ session, bookId, companyId, period, onChanged, onVoucher }: Props) {
  const [reconciling, setReconciling] = useState<string | null>(null);
  const expected = { firmId: session.firm_id, bookId, companyId, period };
  const query = useScopedQuery(
    (signal) =>
      api<unknown>(`/periodic/workspace?book_id=${encodeURIComponent(bookId)}`, session, {
        signal,
      }).then((raw) => parsePeriodic(raw, expected)),
    bookId,
  );
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(''),
    [uncertain, setUncertain] = useState(false);
  const [reasons, setReasons] = useState<Record<string, string>>({}),
    [date, setDate] = useState(monthEnd(period));
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const active = useRef(true),
    inFlight = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function command(path: string, body?: unknown) {
    if (inFlight.current || uncertain) return;
    inFlight.current = true;
    setBusy(true);
    setNotice('');
    setConfirmation(null);
    try {
      await api(path, session, { method: 'POST', body });
      if (active.current) {
        setNotice('已完成本次操作，请核对下方最新记录。');
        query.reload();
        if (path.endsWith('/post')) onChanged?.();
      }
    } catch (failure) {
      if (active.current) {
        setUncertain(true);
        setNotice(`${errorMessage(failure)} 请重新读取并核对结果，不会自动重发。`);
        query.reload();
      }
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  }
  const data = query.data;
  if (reconciling)
    return (
      <AssetReconciliation
        key={reconciling}
        session={session}
        bookId={bookId}
        companyId={companyId}
        period={period}
        assetId={reconciling}
        onVoucher={onVoucher}
        onChanged={query.reload}
        onBack={() => {
          setReconciling(null);
          query.reload();
        }}
      />
    );
  return (
    <>
      <p>资产卡片 → 生成草稿 → 另一位会计独立复核 → 正式记账。查看和生成草稿不会自动记账或导出。</p>
      <button
        type="button"
        className="button secondary"
        disabled={busy || query.loading}
        onClick={() => {
          setConfirmation(null);
          query.reload();
        }}
      >
        重新读取办理结果
      </button>
      {query.loading && <p role="status">正在读取资产和期末草稿…</p>}
      {query.error && <p role="alert">{query.error}</p>}
      {notice && <p role="status">{notice}</p>}
      {uncertain && data && !query.loading && (
        <button
          type="button"
          className="button secondary"
          onClick={() => {
            setUncertain(false);
            setNotice('请核对列表后选择下一项操作。');
          }}
        >
          已核对最新结果，继续人工办理
        </button>
      )}
      {data && (
        <>
          {data.locked && (
            <p role="status">本期已锁定，仅供查看；如需纠错，请从原凭证发起既有更正流程。</p>
          )}
          {!data.can_generate && !data.locked && (
            <p>当前为只读查看；请联系本账套有复核权限的会计办理。</p>
          )}
          {data.can_generate && (
            <AssetForm
              data={data}
              bookId={bookId}
              period={period}
              disabled={busy || uncertain}
              onSave={(body) => command('/periodic/assets', body)}
            />
          )}
          <h3>资产卡片</h3>
          {!data.assets.length && (
            <p>尚无本账套资产卡片。仅需对确有依据的资产建卡，不因此补造期初或采购凭证。</p>
          )}
          {data.assets.map((asset) => (
            <article className="review-section" key={asset.id}>
              <h4>{asset.name}</h4>
              <p>
                {asset.kind === 'fixed' ? '固定资产' : '无形资产'} · 起始 {asset.start_period} ·{' '}
                {asset.life_months} 个月
              </p>
              <p>
                建卡月份 {asset.origin_period} · 当前办理 {period}，沿用同一卡片和已记账历史。
              </p>
              <p>
                原值 {amount(asset.cost_cents)} 元 · 残值 {amount(asset.residual_cents)} 元 ·
                已记账计提 {amount(asset.posted_cents)} 元
              </p>
              {data.can_generate && asset.status === 'active' && (
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy || uncertain}
                  onClick={() =>
                    command(
                      `/periodic/assets/${encodeURIComponent(asset.id)}/generate?book_id=${encodeURIComponent(bookId)}`,
                    )
                  }
                >
                  生成或查看本期草稿
                </button>
              )}
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => setReconciling(asset.id)}
              >
                核对历史差异与科目版本
              </button>
            </article>
          ))}
          <h3>期末草稿</h3>
          {!data.drafts.length && <p>暂无期末草稿。生成后请先核对分录，再交另一位会计复核。</p>}
          {data.drafts.map((draft) => (
            <article className="review-section" key={draft.id}>
              <h4>{draft.description}</h4>
              <p>
                {
                  {
                    draft: '待独立复核',
                    approved: '已复核，待记账',
                    posted: '已正式记账',
                    rejected: '已退回',
                  }[draft.status]
                }{' '}
                · 版本 {draft.version}
              </p>
              <div
                className="finance-lines"
                role="region"
                aria-label={`${draft.description}分录`}
                tabIndex={0}
              >
                <table>
                  <thead>
                    <tr>
                      <th>科目</th>
                      <th>借方</th>
                      <th>贷方</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.lines.map((line) => (
                      <tr key={line.id}>
                        <td>
                          {data.accounts.find((a) => a.id === line.account_id)?.name ??
                            line.account_id}{' '}
                          · v{line.account_version}
                        </td>
                        <td>{amount(line.debit_cents)}</td>
                        <td>{amount(line.credit_cents)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {draft.status === 'draft' && draft.created_by === session.user_id && (
                <p>本版本由你制单，请交另一位有复核权限的会计审核。</p>
              )}
              {(draft.can_approve || draft.can_post) && (
                <label>
                  办理理由
                  <input
                    value={reasons[draft.id] ?? ''}
                    maxLength={1000}
                    onChange={(event) =>
                      setReasons((old) => ({ ...old, [draft.id]: event.target.value }))
                    }
                  />
                </label>
              )}
              {draft.can_approve && (
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy || uncertain || (reasons[draft.id]?.trim().length ?? 0) < 3}
                  onClick={() =>
                    command(`/periodic/drafts/${encodeURIComponent(draft.id)}/approve`, {
                      expected_version: draft.version,
                      reason: reasons[draft.id],
                    })
                  }
                >
                  核对分录后复核
                </button>
              )}
              {draft.can_post && (
                <>
                  <label>
                    记账日期
                    <input
                      type="date"
                      value={date}
                      min={`${period}-01`}
                      max={monthEnd(period)}
                      onChange={(event) => setDate(event.target.value)}
                    />
                  </label>
                  {confirmation !== draft.id ? (
                    <button
                      type="button"
                      className="button secondary"
                      disabled={busy || uncertain || (reasons[draft.id]?.trim().length ?? 0) < 3}
                      onClick={() => setConfirmation(draft.id)}
                    >
                      准备正式记账
                    </button>
                  ) : (
                    <div role="group" aria-label="确认期末记账">
                      <p>将按当前已复核版本正式记账，原记录保留。确认分录及日期无误后继续。</p>
                      <button
                        type="button"
                        className="button"
                        disabled={busy || uncertain || !date.startsWith(`${period}-`)}
                        onClick={() =>
                          command(`/periodic/drafts/${encodeURIComponent(draft.id)}/post`, {
                            expected_version: draft.version,
                            command_key: `periodic-ui-${draft.id}-${draft.version}`,
                            voucher_date: date,
                            reason: reasons[draft.id],
                          })
                        }
                      >
                        确认正式记账
                      </button>
                      <button
                        type="button"
                        className="button secondary"
                        onClick={() => setConfirmation(null)}
                      >
                        取消
                      </button>
                    </div>
                  )}
                </>
              )}
            </article>
          ))}
        </>
      )}
    </>
  );
}
function AssetForm({
  data,
  bookId,
  period,
  disabled,
  onSave,
}: {
  data: PeriodicWorkspace;
  bookId: string;
  period: string;
  disabled: boolean;
  onSave: (body: unknown) => Promise<void>;
}) {
  return (
    <details className="review-section">
      <summary>登记资产卡片</summary>
      <form
        className="finance-asset-form"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const expense = data.accounts.find((a) => a.id === form.get('expense')),
            accum = data.accounts.find((a) => a.id === form.get('accum'));
          if (!expense || !accum) return;
          void onSave({
            book_id: bookId,
            name: form.get('name'),
            kind: form.get('kind'),
            cost: form.get('cost'),
            residual: form.get('residual'),
            life_months: Number(form.get('life')),
            start_period: form.get('start'),
            expense_account_id: expense.id,
            expense_account_version: expense.version,
            accum_account_id: accum.id,
            accum_account_version: accum.version,
            reason: form.get('reason'),
          });
        }}
      >
        <fieldset disabled={disabled}>
          <legend>依照已确认的资产资料填写；建卡不生成采购凭证</legend>
          <label>
            资产名称
            <input name="name" required maxLength={120} />
          </label>
          <label>
            资产类型
            <select name="kind">
              <option value="fixed">固定资产</option>
              <option value="intangible">无形资产</option>
            </select>
          </label>
          <label>
            原值（元）
            <input name="cost" inputMode="decimal" required pattern="[0-9]+(\.[0-9]{1,2})?" />
          </label>
          <label>
            预计残值（元）
            <input
              name="residual"
              defaultValue="0.00"
              inputMode="decimal"
              required
              pattern="[0-9]+(\.[0-9]{1,2})?"
            />
          </label>
          <label>
            使用月数
            <input name="life" type="number" required min={1} max={600} step={1} />
          </label>
          <label>
            开始计提月份
            <input name="start" type="month" required defaultValue={period} />
          </label>
          {(['expense', 'accum'] as const).map((key) => (
            <label key={key}>
              {key === 'expense' ? '费用科目' : '累计折旧／摊销科目'}
              <select name={key} required defaultValue="">
                <option value="">请选择已确认科目</option>
                {data.accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.code} {a.name} · v{a.version}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <label>
            登记依据与理由
            <input name="reason" required minLength={3} maxLength={1000} />
          </label>
          <button type="submit" className="button secondary">
            保存资产卡片
          </button>
        </fieldset>
      </form>
    </details>
  );
}
