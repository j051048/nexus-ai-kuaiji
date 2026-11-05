import { useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { useScopedQuery } from './useScopedQuery';
import type { Session } from './types';
import type { LedgerOverview } from './ledger-types';
import { parseOpeningLines, type OpeningAccount } from './opening-preparation';

type Policy = {
  id: string;
  ledger_id: string;
  standard_kind: string;
  taxpayer_kind: string;
  effective_from: string;
  effective_to: string | null;
  status: string;
};
type Package = { id: string; ledger_id: string; target_period: string; policy_id: string };
type Version = { id: string; version: number; status: string; submitted_by: string | null };
const labels: Record<string, string> = {
  draft: '待校验',
  validated: '已校验',
  validation_failed: '校验未通过',
  submitted: '待独立核准',
  approved: '已核准',
  frozen: '已冻结',
  superseded: '已替代',
};

/** Thin forms over the existing opening service. No generated balances or automatic approvals. */
export function OpeningPreparationPanel({
  session,
  companyId,
  bookId,
  period,
  step,
  onBusyChange,
}: {
  session: Session;
  companyId: string;
  bookId: string;
  period: string;
  step: string;
  onBusyChange: (busy: boolean) => void;
}) {
  const [reason, setReason] = useState(''),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false);
  const [standard, setStandard] = useState('small_enterprise'),
    [taxpayer, setTaxpayer] = useState('small_scale');
  const [code, setCode] = useState(''),
    [name, setName] = useState(''),
    [category, setCategory] = useState('asset'),
    [normal, setNormal] = useState('debit');
  const [csv, setCsv] = useState(''),
    [selectedPolicy, setPolicy] = useState(''),
    [selectedPackage, setPackage] = useState('');
  const [mapCode, setMapCode] = useState(''),
    [mapAccount, setMapAccount] = useState('');
  const [inspection, setInspection] = useState<string[]>([]);
  const lock = useRef(false),
    importKey = useRef(crypto.randomUUID());
  const scope = `${session.scope_key}:${companyId}:${bookId}:${period}`;
  const query = useScopedQuery(async (signal) => {
    const ledger = await api<LedgerOverview>(
      `/books/${encodeURIComponent(bookId)}/ledger`,
      session,
      { signal },
    );
    if (
      ledger.book_id !== bookId ||
      ledger.company_id !== companyId ||
      ledger.period !== period ||
      !ledger.can_manage
    )
      throw new Error('当前核算主体或授权无法核验，请联系账套管理员。');
    const q = `?ledger_id=${encodeURIComponent(ledger.id)}`;
    const [policies, accounts, packages, mappings] = await Promise.all([
      api<{ policies: Policy[] }>(`/opening/policies${q}`, session, { signal }),
      api<{ accounts: OpeningAccount[] }>(`/opening/accounts${q}`, session, { signal }),
      api<{ packages: Package[] }>(`/opening/packages${q}`, session, { signal }),
      api<{
        mappings: {
          book_id: string;
          book_account_code: string;
          account_id: string;
          account_version: number;
        }[];
      }>(`/opening/mappings${q}&book_id=${encodeURIComponent(bookId)}`, session, { signal }),
    ]);
    if (
      policies.policies.some((row) => row.ledger_id !== ledger.id) ||
      packages.packages.some((row) => row.ledger_id !== ledger.id)
    )
      throw new Error('期初数据范围不匹配。');
    if (mappings.mappings.some((row) => row.book_id !== bookId))
      throw new Error('科目映射范围不匹配。');
    return {
      ledger,
      policies: policies.policies,
      accounts: accounts.accounts,
      mappings: mappings.mappings,
      packages: packages.packages.filter((row) => row.target_period === period),
    };
  }, scope);
  const packageQuery = useScopedQuery(
    async (signal) => {
      if (!selectedPackage || !query.data?.packages.some((row) => row.id === selectedPackage))
        return null;
      const value = await api<{ package: Package; versions: Version[] }>(
        `/opening/packages/${encodeURIComponent(selectedPackage)}`,
        session,
        { signal },
      );
      if (
        value.package.id !== selectedPackage ||
        value.package.ledger_id !== query.data.ledger.id ||
        value.package.target_period !== period
      )
        throw new Error('期初包范围已变化。');
      return value;
    },
    `${scope}:${selectedPackage}:${query.data?.packages.map((row) => row.id).join(',')}`,
  );
  async function command(path: string, body: object) {
    if (lock.current || uncertain || !query.data || reason.trim().length < 3) return;
    lock.current = true;
    setBusy(true);
    onBusyChange(true);
    setError('');
    setNotice('');
    setInspection([]);
    try {
      const result = await api<{
        checks?: { check_code: string; status: string; details_json: string }[];
      }>(path, session, { method: 'POST', body });
      const failed = result.checks?.filter((row) => row.status !== 'pass') ?? [];
      setInspection(
        failed.map(
          (row) =>
            `${({ balance: '借贷平衡', line_math: '金额衔接', config_current: '科目版本', auxiliary: '辅助维度', config_fingerprint: '配置版本' } as Record<string, string>)[row.check_code] || row.check_code}：未通过，请核对期初明细与配置。`,
        ),
      );
      setNotice(
        failed.length
          ? '校验未通过，请修正后再次校验；尚未提交复核。'
          : '已保存，请核对记录后继续下一步。',
      );
      query.reload();
      packageQuery.reload();
    } catch (failure) {
      setError(errorMessage(failure) + ' 请重新读取记录核对结果，不会自动重发。');
      setUncertain(true);
    } finally {
      lock.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }
  const data = query.data,
    disabled = busy || uncertain || !data || reason.trim().length < 3;
  return (
    <section aria-label="期初办理" className="review-section opening-preparation">
      <h3>
        {step === 'template'
          ? '确认核算政策'
          : step === 'accounts'
            ? '登记正式科目'
            : '准备与复核期初'}
      </h3>
      <p>当前月份：{period}。只登记真实数据，校验、提交、独立核准、冻结与绑定分别办理。</p>
      {(query.error || packageQuery.error || error) && (
        <p role="alert" className="form-error">
          {query.error || packageQuery.error || error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!!inspection.length && (
        <ul role="alert">
          {inspection.map((row) => (
            <li key={row}>{row}</li>
          ))}
        </ul>
      )}
      <button
        className="text-button"
        disabled={busy}
        onClick={() => {
          setUncertain(false);
          query.reload();
          packageQuery.reload();
        }}
      >
        重新读取记录
      </button>
      <label>
        本次办理依据
        <input
          value={reason}
          maxLength={1000}
          disabled={busy}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {data && step === 'template' && (
        <>
          <ul>
            {data.policies.map((row) => (
              <li key={row.id}>
                {row.standard_kind === 'small_enterprise' ? '小企业会计准则' : '企业会计准则'} ·{' '}
                {row.effective_from} 至 {row.effective_to || '长期'} ·{' '}
                {row.status === 'active' ? '有效' : '已停用'}
              </li>
            ))}
          </ul>
          <label>
            会计准则
            <select value={standard} onChange={(e) => setStandard(e.target.value)} disabled={busy}>
              <option value="small_enterprise">小企业会计准则</option>
              <option value="enterprise">企业会计准则</option>
            </select>
          </label>
          <label>
            纳税人身份
            <select value={taxpayer} onChange={(e) => setTaxpayer(e.target.value)} disabled={busy}>
              <option value="small_scale">小规模纳税人</option>
              <option value="general">一般纳税人</option>
              <option value="non_taxpayer">非纳税主体</option>
            </select>
          </label>
          <button
            className="button"
            disabled={disabled}
            onClick={() =>
              void command('/opening/policies', {
                ledger_id: data.ledger.id,
                standard_kind: standard,
                taxpayer_kind: taxpayer,
                effective_from: period,
                reason,
              })
            }
          >
            确认从本月生效
          </button>
        </>
      )}
      {data && step === 'accounts' && (
        <>
          <ul>
            {data.accounts.map((row) => (
              <li key={row.id}>
                {row.code} {row.name} · V{row.current_version}
              </li>
            ))}
          </ul>
          <label>
            科目编码
            <input value={code} maxLength={30} onChange={(e) => setCode(e.target.value)} />
          </label>
          <label>
            科目名称
            <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            科目类别
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {Object.entries({
                asset: '资产',
                liability: '负债',
                equity: '权益',
                cost: '成本',
                income: '收入',
                expense: '费用',
                other: '其他',
              }).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            正常余额方向
            <select value={normal} onChange={(e) => setNormal(e.target.value)}>
              <option value="debit">借方</option>
              <option value="credit">贷方</option>
            </select>
          </label>
          <button
            className="button"
            disabled={disabled || !code || !name}
            onClick={() =>
              void command('/opening/accounts', {
                ledger_id: data.ledger.id,
                code,
                name,
                category,
                normal_balance: normal,
                effective_from: period,
                reason,
              })
            }
          >
            登记科目
          </button>
          <h4>关联本账套科目</h4>
          <ul>
            {data.mappings.map((row) => (
              <li key={row.book_account_code}>
                {row.book_account_code} →{' '}
                {data.accounts.find((a) => a.id === row.account_id)?.code || '已登记科目'} · V
                {row.account_version}
              </li>
            ))}
          </ul>
          <label>
            账套科目编码
            <input value={mapCode} disabled={busy} onChange={(e) => setMapCode(e.target.value)} />
          </label>
          <label>
            对应正式科目
            <select
              value={mapAccount}
              disabled={busy}
              onChange={(e) => setMapAccount(e.target.value)}
            >
              <option value="">请选择</option>
              {data.accounts
                .filter((a) => a.status === 'active')
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.code} {a.name} · V{a.current_version}
                  </option>
                ))}
            </select>
          </label>
          <button
            className="button secondary"
            disabled={disabled || !mapCode || !mapAccount}
            onClick={() =>
              void command('/opening/mappings', {
                book_id: bookId,
                book_account_code: mapCode,
                account_id: mapAccount,
                account_version: data.accounts.find((a) => a.id === mapAccount)?.current_version,
                reason,
              })
            }
          >
            保存科目关联
          </button>
        </>
      )}
      {data && step === 'opening' && (
        <>
          {!data.packages.length && (
            <>
              <label>
                采用政策
                <select value={selectedPolicy} onChange={(e) => setPolicy(e.target.value)}>
                  <option value="">请选择</option>
                  {data.policies
                    .filter(
                      (row) =>
                        row.status === 'active' &&
                        row.effective_from <= period &&
                        (!row.effective_to || row.effective_to >= period),
                    )
                    .map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.effective_from} · {row.standard_kind}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="button secondary"
                disabled={disabled || !selectedPolicy}
                onClick={() =>
                  void command('/opening/packages', {
                    ledger_id: data.ledger.id,
                    target_period: period,
                    policy_id: selectedPolicy,
                  })
                }
              >
                建立本月期初包
              </button>
            </>
          )}
          <label>
            本月期初包
            <select value={selectedPackage} onChange={(e) => setPackage(e.target.value)}>
              <option value="">请选择</option>
              {data.packages.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.target_period} 期初包
                </option>
              ))}
            </select>
          </label>
          <label>
            期初余额（每行：科目编码,年初余额,本年借方,本年贷方,期初余额）
            <textarea
              rows={5}
              value={csv}
              onChange={(e) => {
                setCsv(e.target.value);
                importKey.current = crypto.randomUUID();
              }}
            />
          </label>
          <p>
            贷方余额用负数；需要辅助维度或往来未结明细时请先交由会计完整核对，校验不通过不能提交。
          </p>
          <button
            className="button secondary"
            disabled={disabled || !selectedPackage || !csv}
            onClick={() => {
              try {
                void command(`/opening/packages/${encodeURIComponent(selectedPackage)}/versions`, {
                  idempotency_key: importKey.current,
                  reason,
                  lines: parseOpeningLines(csv, data.accounts),
                });
              } catch (failure) {
                setError(errorMessage(failure));
              }
            }}
          >
            保存期初草稿
          </button>
          {packageQuery.data?.versions.map((version) => (
            <article key={version.id}>
              <strong>
                V{version.version} · {labels[version.status] || version.status}
              </strong>
              {(['validate', 'submit', 'approve', 'freeze', 'bind'] as const).map((action) => {
                const allowed =
                  action === 'validate'
                    ? ['draft', 'validated', 'validation_failed'].includes(version.status)
                    : action === 'submit'
                      ? version.status === 'validated'
                      : action === 'approve'
                        ? version.status === 'submitted' && version.submitted_by !== session.user_id
                        : action === 'freeze'
                          ? version.status === 'approved'
                          : version.status === 'frozen';
                return (
                  <button
                    key={action}
                    className="button secondary"
                    disabled={disabled || !allowed}
                    onClick={() =>
                      void command(
                        action === 'bind'
                          ? '/posting/opening-bindings'
                          : `/opening/versions/${encodeURIComponent(version.id)}/${action}`,
                        action === 'bind'
                          ? { book_id: bookId, package_version_id: version.id, reason }
                          : action === 'validate'
                            ? {}
                            : { expected_version: version.version, reason },
                      )
                    }
                  >
                    {
                      {
                        validate: '校验',
                        submit: '提交复核',
                        approve: '独立核准',
                        freeze: '冻结',
                        bind: '绑定本月账套',
                      }[action]
                    }
                  </button>
                );
              })}
            </article>
          ))}
        </>
      )}
    </section>
  );
}
