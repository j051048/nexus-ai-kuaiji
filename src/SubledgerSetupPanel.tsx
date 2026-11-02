import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import './formal-close.css';

type Panel = 'subledger' | 'bank';
interface Props {
  session: Session;
  bookId: string;
  period: string;
  panel: Panel;
  accountLabels?: Record<string, string>;
  onClose: () => void;
  onChanged: () => void;
}

interface ControlAccount {
  id: string;
  kind: string;
  control_account_id: string;
  control_account_version: number;
  required_aux: string;
  effective_from: string;
  effective_to: string;
}
interface BankAccount {
  id: string;
  name: string;
  ledger_account_id: string;
  ledger_account_version: number;
}
interface BankBatch {
  preview_id: string;
  file_id: string;
  sha256: string;
  filename: string;
  confirmed_at: string;
}

const kinds: [string, string][] = [
  ['receivable', '应收账款'],
  ['payable', '应付账款'],
  ['advance_received', '预收账款'],
  ['advance_paid', '预付账款'],
];
const kindLabel = (kind: string) => kinds.find(([value]) => value === kind)?.[1] ?? kind;
const objects = (value: unknown, key: string): Record<string, unknown>[] => {
  if (!value || typeof value !== 'object') return [];
  const list = (value as Record<string, unknown>)[key];
  return Array.isArray(list)
    ? list.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    : [];
};
const text = (value: unknown) => (typeof value === 'string' ? value : '');

/** Configuration that a blocked close check points at. Every write is sent to
 * the server and re-read afterwards; nothing is marked done locally. */
export function SubledgerSetupPanel({
  session,
  bookId,
  period,
  panel,
  accountLabels = {},
  onClose,
  onChanged,
}: Props) {
  const [controls, setControls] = useState<ControlAccount[]>([]);
  const [banks, setBanks] = useState<BankAccount[]>([]);
  const [batches, setBatches] = useState<BankBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      setError('');
      try {
        const query = `?book_id=${encodeURIComponent(bookId)}`;
        const [controlRaw, bankRaw, batchRaw] = await Promise.all([
          api<unknown>(
            `/subledger/control-accounts${query}&as_of=${encodeURIComponent(period)}`,
            session,
          ),
          api<unknown>(`/subledger/bank-accounts${query}`, session),
          api<unknown>(`/subledger/bank-batches${query}`, session),
        ]);
        if (!live.current) return;
        setControls(
          objects(controlRaw, 'accounts').map((row) => ({
            id: text(row.id),
            kind: text(row.kind),
            control_account_id: text(row.control_account_id),
            control_account_version: Number(row.control_account_version ?? 0),
            required_aux: Array.isArray(row.required_aux)
              ? row.required_aux
                  .filter((item): item is string => typeof item === 'string')
                  .join(' / ')
              : '',
            effective_from: text(row.effective_from),
            effective_to: text(row.effective_to),
          })),
        );
        setBanks(
          objects(bankRaw, 'accounts').map((row) => ({
            id: text(row.id),
            name: text(row.name),
            ledger_account_id: text(row.ledger_account_id),
            ledger_account_version: Number(row.ledger_account_version ?? 0),
          })),
        );
        setBatches(
          objects(batchRaw, 'batches').map((row) => ({
            preview_id: text(row.preview_id),
            file_id: text(row.file_id),
            sha256: text(row.sha256),
            filename: text(row.filename),
            confirmed_at: text(row.confirmed_at),
          })),
        );
      } catch (failure) {
        if (live.current) setError(`${errorMessage(failure)} 请重新读取后再配置。`);
      } finally {
        if (live.current && !quiet) setLoading(false);
      }
    },
    [bookId, period, session],
  );
  useEffect(() => {
    void load();
  }, [load]);
  async function submit(path: string, body: unknown, success: string) {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api<unknown>(path, session, { method: 'POST', body });
      if (!live.current) return;
      setNotice(success);
      await load(true);
      onChanged();
    } catch (failure) {
      if (live.current) setError(`${errorMessage(failure)} 未保存任何配置；请核对当前版本后重试。`);
    } finally {
      if (live.current) setBusy(false);
    }
  }
  async function submitForm(
    event: React.FormEvent<HTMLFormElement>,
    path: string,
    build: (data: FormData) => Record<string, unknown>,
    success: string,
  ) {
    event.preventDefault();
    const form = event.currentTarget;
    await submit(path, build(new FormData(form)), success);
    form.reset();
  }
  const label = (id: string) => accountLabels[id] ?? '';
  const section = useRef<HTMLElement | null>(null);
  // The blocked row lives at the bottom of a long close panel; bring the
  // freshly opened workspace into view so the click visibly lands somewhere.
  useEffect(() => {
    section.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [panel]);

  return (
    <section className="formal-close-setup" aria-label="月结阻断办理" ref={section}>
      <header>
        <div>
          <h3>{panel === 'subledger' ? '控制科目配置' : '银行账户与覆盖证据'}</h3>
          <p>{period} · 当前账套。保存后回到月结面板重新读取，再生成预检；配置本身不会通过检查。</p>
        </div>
        <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
          收起
        </button>
      </header>
      {loading && <p role="status">正在读取当前配置…</p>}
      {error && (
        <div role="alert" className="inline-error">
          <p>{error}</p>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => void load()}
          >
            重新读取配置
          </button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!loading && !error && panel === 'subledger' && (
        <>
          <div className="formal-close-setup-list">
            <h4>本月生效的控制科目</h4>
            {controls.length === 0 ? (
              <p>还没有适用的控制科目配置。</p>
            ) : (
              <ul>
                {controls.map((row) => (
                  <li key={row.id}>
                    {kindLabel(row.kind)} · {label(row.control_account_id)} {row.control_account_id}{' '}
                    · V{row.control_account_version} · 生效 {row.effective_from}
                    {row.effective_to ? ` 至 ${row.effective_to}` : ' 起'}
                    {row.required_aux ? ` · 必填辅助：${row.required_aux}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <form
            className="formal-close-setup-form"
            onSubmit={(event) =>
              void submitForm(
                event,
                '/subledger/control-accounts',
                (data) => ({
                  book_id: bookId,
                  kind: String(data.get('kind') ?? ''),
                  control_account_id: String(data.get('control_account_id') ?? '').trim(),
                  control_account_version: Number(data.get('control_account_version') ?? 0),
                  required_aux: String(data.get('required_aux') ?? '')
                    .split(/[,，\s]+/)
                    .filter(Boolean),
                  effective_from: period,
                  reason: String(data.get('reason') ?? '').trim(),
                }),
                '控制科目配置已保存；请重新读取月结证据。',
              )
            }
          >
            <h4>配置控制科目</h4>
            <label>
              类别
              <select name="kind" required>
                {kinds.map(([value, name]) => (
                  <option key={value} value={value}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              科目
              <input
                name="control_account_id"
                required
                maxLength={100}
                list="formal-close-accounts"
              />
            </label>
            <datalist id="formal-close-accounts">
              {Object.entries(accountLabels).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </datalist>
            <label>
              科目版本
              <input name="control_account_version" type="number" min={1} required />
            </label>
            <label>
              必填辅助项（可空）
              <input name="required_aux" maxLength={200} placeholder="以逗号分隔，例如 客户,项目" />
            </label>
            <label>
              配置说明（至少 3 字）
              <input name="reason" required minLength={3} maxLength={1000} />
            </label>
            <button type="submit" className="button" disabled={busy || loading}>
              保存控制科目配置
            </button>
          </form>
          <form
            className="formal-close-setup-form"
            onSubmit={(event) =>
              void submitForm(
                event,
                '/subledger/control-applicability',
                (data) => ({
                  book_id: bookId,
                  status: 'not_applicable',
                  effective_from: period,
                  reason: String(data.get('reason') ?? '').trim(),
                }),
                '已登记本月不适用；请重新读取月结证据确认。',
              )
            }
          >
            <h4>确认本月没有控制科目业务</h4>
            <p>只有确实没有往来余额与发生额时才可使用；空配置不能跳过检查。</p>
            <label>
              说明（至少 3 字）
              <input name="reason" required minLength={3} maxLength={1000} />
            </label>
            <button type="submit" className="button secondary" disabled={busy || loading}>
              登记本月不适用
            </button>
          </form>
        </>
      )}
      {!loading && !error && panel === 'bank' && (
        <>
          <div className="formal-close-setup-list">
            <h4>已映射银行账户</h4>
            {banks.length === 0 ? (
              <p>还没有银行账户映射；先建立映射，再登记本月覆盖。</p>
            ) : (
              <ul>
                {banks.map((row) => (
                  <li key={row.id}>
                    {row.name} · 对应 {label(row.ledger_account_id)} {row.ledger_account_id} · V
                    {row.ledger_account_version}
                  </li>
                ))}
              </ul>
            )}
            <h4>已确认的银行导入批次</h4>
            {batches.length === 0 ? (
              <p>本月还没有已确认的银行流水导入批次；覆盖必须绑定原件的 SHA-256。</p>
            ) : (
              <ul>
                {batches.map((row) => (
                  <li key={row.preview_id}>
                    {row.filename || row.file_id} · 确认于 {row.confirmed_at} · SHA-256{' '}
                    {row.sha256.slice(0, 12)}…
                  </li>
                ))}
              </ul>
            )}
          </div>
          <form
            className="formal-close-setup-form"
            onSubmit={(event) =>
              void submitForm(
                event,
                '/subledger/bank-accounts',
                (data) => ({
                  book_id: bookId,
                  name: String(data.get('name') ?? '').trim(),
                  ledger_account_id: String(data.get('ledger_account_id') ?? '').trim(),
                  ledger_account_version: Number(data.get('ledger_account_version') ?? 0),
                  reason: String(data.get('reason') ?? '').trim(),
                }),
                '银行账户映射已保存；请重新读取后再登记覆盖。',
              )
            }
          >
            <h4>建立银行账户映射</h4>
            <label>
              账户名称
              <input name="name" required maxLength={120} />
            </label>
            <label>
              对应科目
              <input
                name="ledger_account_id"
                required
                maxLength={100}
                list="formal-close-accounts"
              />
            </label>
            <label>
              科目版本
              <input name="ledger_account_version" type="number" min={1} required />
            </label>
            <label>
              说明（至少 3 字）
              <input name="reason" required minLength={3} maxLength={1000} />
            </label>
            <button type="submit" className="button secondary" disabled={busy || loading}>
              保存银行账户映射
            </button>
          </form>
          <form
            className="formal-close-setup-form"
            onSubmit={(event) => {
              const form = event.currentTarget;
              const batch = batches.find((item) => item.preview_id === form.batch.value);
              if (!batch) {
                event.preventDefault();
                setError('请选择已确认的银行导入批次；覆盖必须绑定原件与 SHA-256。');
                return;
              }
              void submitForm(
                event,
                '/subledger/bank-coverages',
                (data) => ({
                  book_id: bookId,
                  account_id: String(data.get('account_id') ?? ''),
                  period,
                  opening: String(data.get('opening') ?? '').trim(),
                  closing: String(data.get('closing') ?? '').trim(),
                  source_ref: batch.filename,
                  source_file_id: batch.file_id,
                  source_preview_id: batch.preview_id,
                  source_sha256: batch.sha256,
                  reason: String(data.get('reason') ?? '').trim(),
                }),
                '覆盖已登记，等待另一位复核员核验原件。',
              );
            }}
          >
            <h4>登记本月银行覆盖</h4>
            {banks.length === 0 || batches.length === 0 ? (
              <p>需要先有银行账户映射与已确认的导入批次，才能登记覆盖。</p>
            ) : (
              <>
                <label>
                  银行账户
                  <select name="account_id" required>
                    {banks.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  已确认导入批次
                  <select name="batch" required>
                    {batches.map((row) => (
                      <option key={row.preview_id} value={row.preview_id}>
                        {row.filename || row.file_id} · {row.confirmed_at}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  期初余额
                  <input name="opening" required inputMode="decimal" placeholder="例如 12345.67" />
                </label>
                <label>
                  期末余额
                  <input name="closing" required inputMode="decimal" placeholder="例如 23456.78" />
                </label>
                <label>
                  说明（至少 3 字）
                  <input name="reason" required minLength={3} maxLength={1000} />
                </label>
                <button type="submit" className="button" disabled={busy || loading}>
                  登记覆盖
                </button>
              </>
            )}
          </form>
        </>
      )}
    </section>
  );
}
