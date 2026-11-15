import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download, FileCheck2, FileUp, Link2, LoaderCircle, Upload } from 'lucide-react';
import { api, apiBlob, errorMessage } from './api';
import type { Session } from './types';
import { uploadSizeError } from './transport-limits';
import './integration.css';

type Mapping = { local_code: string; external_code: string };
type Config = {
  version: number;
  external_book_id: string;
  product_version: string;
  deployment: 'unknown' | 'on_premise' | 'tplus_cloud';
  accounts: Mapping[];
  auxiliaries: { kind: string; local_value: string; external_code: string }[];
};
type Preview = {
  id: string;
  fingerprint: string;
  state: string;
  record_count: number;
  duplicates: number;
  conflicts: number;
  records: { kind: string; external_id: string; name: string; period: string }[];
};
type Overview = {
  configuration: Config | null;
  can_configure: boolean;
  can_review: boolean;
  period: string;
  pagination: { has_more: boolean; total: number };
  imports: { id: string; state: string; record_count: number; created_at: string }[];
  outbox: { id: string; export_id: string; state: string; external_code: string }[];
};
type Intent = { id: string; package: unknown };
type Artifact = {
  id: string;
  outbox_id: string;
  profile: string;
  filename: string;
  row_count: number;
  voucher_count: number;
  content_sha256: string;
  created_at: string;
  synthetic: boolean;
  native_tplus_format: boolean;
};
type Receipt = {
  id: string;
  artifact_id: string;
  receipt_code: string;
  result_status: 'accepted' | 'rejected' | 'duplicate' | 'unknown' | 'partial';
  accepted_count: number;
  rejected_count: number;
  duplicate_count: number;
  unknown_count: number;
  external_posting: boolean;
  created_at: string;
};

const VENDOR_TEMPLATES = [
  { id: 'neutral', label: '中性模板（可直接导入）', filename: 'tplus-reference-template.json' },
  { id: 'yonyou_tplus', label: '用友 T+ 导出参考', filename: 'yonyou-tplus-reference.json' },
  {
    id: 'kingdee_jingdouyun',
    label: '金蝶精斗云导出参考',
    filename: 'kingdee-jingdouyun-reference.json',
  },
  {
    id: 'kingdee_yunxingchen',
    label: '金蝶云·星辰导出参考',
    filename: 'kingdee-yunxingchen-reference.json',
  },
  { id: 'ningmengyun', label: '柠檬云导出参考', filename: 'ningmengyun-reference.json' },
] as const;

function download(value: unknown, filename: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function IntegrationWorkbench({
  session,
  bookId,
  onBack,
  onBusyChange,
  embedded = false,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (busy: boolean) => void;
  embedded?: boolean;
}) {
  const base = `/books/${encodeURIComponent(bookId)}/integrations/tplus`;
  const [data, setData] = useState<Overview | null>(null),
    [preview, setPreview] = useState<Preview | null>(null);
  const [exports, setExports] = useState<{ id: string; filename: string }[]>([]);
  const [externalBook, setExternalBook] = useState(''),
    [version, setVersion] = useState('待确认'),
    [deployment, setDeployment] = useState<Config['deployment']>('unknown');
  const [mappings, setMappings] = useState(''),
    [auxiliaries, setAuxiliaries] = useState(''),
    [reason, setReason] = useState(''),
    [exportId, setExportId] = useState(''),
    [vendor, setVendor] = useState<string>('neutral');
  const [file, setFile] = useState<File | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [artifact, setArtifact] = useState<Artifact | null>(null),
    [receipts, setReceipts] = useState<Receipt[]>([]),
    [receiptFile, setReceiptFile] = useState<File | null>(null),
    [receiptCode, setReceiptCode] = useState(''),
    [offset, setOffset] = useState(0);
  const live = useRef(true),
    lock = useRef(false),
    epoch = useRef(0),
    keys = useRef<Record<string, string>>({});
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(async () => {
    const id = ++epoch.current;
    try {
      const [overview, list] = await Promise.all([
        api<Overview>(`${base}?limit=20&offset=${offset}`, session),
        api<{ exports: { id: string; filename: string }[] }>(
          `/books/${encodeURIComponent(bookId)}/exports`,
          session,
        ),
      ]);
      if (!live.current || id !== epoch.current) return;
      setData(overview);
      setExports(list.exports);
      const c = overview.configuration;
      if (c) {
        setExternalBook(c.external_book_id);
        setVersion(c.product_version);
        setDeployment(c.deployment);
        setMappings(c.accounts.map((x) => `${x.local_code},${x.external_code}`).join('\n'));
        setAuxiliaries(
          c.auxiliaries.map((x) => `${x.kind},${x.local_value},${x.external_code}`).join('\n'),
        );
      }
    } catch (e) {
      if (live.current && id === epoch.current) setError(errorMessage(e));
    }
  }, [base, bookId, offset, session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await action();
    } catch (e) {
      if (live.current) setError(errorMessage(e));
    } finally {
      lock.current = false;
      if (live.current) setBusy(false);
    }
  }
  function key(kind: string) {
    return (keys.current[kind] ||= crypto.randomUUID());
  }
  function changed() {
    keys.current = {};
    setPreview(null);
  }
  async function configure() {
    await run(async () => {
      const accounts = mappings
        .split('\n')
        .filter((x) => x.trim())
        .map((row) => {
          const values = row.split(',').map((x) => x.trim());
          if (values.length !== 2 || values.some((x) => !x))
            throw new Error('科目映射每行填写“本地科目,外部科目”');
          return { local_code: values[0], external_code: values[1] };
        });
      const aux = auxiliaries
        .split('\n')
        .filter((x) => x.trim())
        .map((row) => {
          const values = row.split(',').map((x) => x.trim());
          if (
            values.length !== 3 ||
            !['department', 'employee', 'counterparty', 'project'].includes(values[0]) ||
            values.some((x) => !x)
          )
            throw new Error('辅助项每行填写“类型,本地值,外部编号”');
          return { kind: values[0], local_value: values[1], external_code: values[2] };
        });
      await api(`${base}/configuration`, session, {
        method: 'POST',
        body: {
          expected_version: data?.configuration?.version ?? 0,
          external_book_id: externalBook,
          product_version: version,
          deployment,
          accounts,
          auxiliaries: aux,
          reason,
          idempotency_key: key('config'),
        },
      });
      if (!live.current) return;
      keys.current = {};
      await load();
      setMessage('新映射版本已保存，旧交付包仍保留原映射。');
    });
  }
  async function importFile() {
    if (!file) return;
    await run(async () => {
      const invalidSize = uploadSizeError(file.size, 2_000_000);
      if (invalidSize) throw new Error(invalidSize);
      if (!/\.(csv|json)$/i.test(file.name)) throw new Error('请选择 UTF-8 JSON 或 CSV 文件');
      const content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      const result = await api<Preview>(`${base}/imports`, session, {
        method: 'POST',
        body: {
          format: /\.csv$/i.test(file.name) ? 'csv' : 'json',
          content,
          reason,
          idempotency_key: key('import'),
        },
      });
      if (live.current) {
        setPreview(result);
        await load();
      }
    });
  }
  async function prepare() {
    await run(async () => {
      const intent = await api<Intent>(`${base}/outbox`, session, {
        method: 'POST',
        body: {
          export_id: exportId,
          configuration_version: data?.configuration?.version,
          reason,
          idempotency_key: key('export'),
        },
      });
      if (live.current) {
        download(intent.package, `tplus-package-${intent.id}.json`);
        await load();
        setMessage('冻结交付包已生成。请按客户软件版本转换并导入，再到回执核验页核对。');
      }
    });
  }
  async function loadArtifact(outboxId: string) {
    await run(async () => {
      const value = await api<{ artifacts: Artifact[] }>(`${base}/outbox/${outboxId}/artifacts`, session);
      if (!live.current) return;
      setArtifact(value.artifacts[0] ?? null);
      setReceipts([]);
      setMessage(
        value.artifacts.length
          ? '已读取冻结适配文件，可下载 CSV 或登记客户回执。'
          : '该交付意图尚未生成 54 列适配文件。',
      );
      if (value.artifacts[0]) {
        const rows = await api<{ receipts: Receipt[] }>(
          `${base}/artifacts/${value.artifacts[0].id}/receipts`,
          session,
        );
        if (live.current) setReceipts(rows.receipts);
      }
    });
  }
  async function makeArtifact(outboxId: string) {
    await run(async () => {
      const value = await api<Artifact>(`${base}/outbox/${outboxId}/artifacts`, session, {
        method: 'POST',
      });
      if (!live.current) return;
      setArtifact(value);
      setReceipts([]);
      setMessage(`54 列合成适配文件已冻结：${value.row_count} 行，${value.voucher_count} 张凭证。`);
      await load();
    });
  }
  async function downloadArtifact() {
    if (!artifact) return;
    await run(async () => {
      const blob = await apiBlob(`${base}/artifacts/${artifact.id}/download`, session);
      if (live.current) downloadBlob(blob, artifact.filename);
    });
  }
  async function submitReceipt() {
    if (!artifact || !receiptFile) return;
    await run(async () => {
      if (uploadSizeError(receiptFile.size, 2_000_000))
        throw new Error(uploadSizeError(receiptFile.size, 2_000_000) ?? '回执文件过大');
      const content = await receiptFile.text();
      const value = await api<Receipt>(`${base}/artifacts/${artifact.id}/receipts`, session, {
        method: 'POST',
        body: {
          receipt_code: receiptCode.trim(),
          content,
          reason,
        },
      });
      if (!live.current) return;
      setReceipts((rows) => [value, ...rows.filter((row) => row.id !== value.id)]);
      setReceiptFile(null);
      setMessage(
        `回执已登记：接受 ${value.accepted_count}、拒绝 ${value.rejected_count}、重复 ${value.duplicate_count}、未知 ${value.unknown_count}。外部过账仍未确认。`,
      );
    });
  }
  const Wrapper = embedded ? 'div' : 'main';
  return (
    <Wrapper className={'integration-workbench' + (embedded ? ' embedded' : '')}>
      {embedded ? (
        <section className="book-embedded-heading">
          <div>
            <h2>财务软件接入</h2>
            <p>核验账套、科目和辅助映射，保留客户导出资料与已审核凭证交付包。</p>
          </div>
          <span className="integration-offline">
            <Link2 size={15} />
            文件模式
          </span>
        </section>
      ) : (
        <header className="integration-heading">
          <button className="button secondary small" disabled={busy} onClick={onBack}>
            <ArrowLeft size={15} />
            返回账套
          </button>
          <span className="section-eyebrow">T+ · 文件接入验证</span>
          <h1>财务软件接入</h1>
          <p>先核验账套与映射，再保留客户导出资料、准备已审核凭证的交付包。</p>
          <span className="integration-offline">
            <Link2 size={15} />
            文件模式 · 真实连接未启用
          </span>
        </header>
      )}
      {error && (
        <div role="alert" className="inline-error">
          {error}
        </div>
      )}
      {message && (
        <p role="status" className="integration-message">
          {message}
        </p>
      )}
      {!data ? (
        <p>
          <LoaderCircle className="spinner" size={16} />
          正在读取接入配置…
        </p>
      ) : (
        <>
          <section className="integration-section">
            <div className="integration-section-title">
              <span>01</span>
              <h2>
                账套与科目映射 <small>版本 {data.configuration?.version ?? 0}</small>
              </h2>
            </div>
            <div className="integration-fields">
              <label>
                客户 T+ 账套标识
                <input
                  disabled={busy || !data.can_configure}
                  value={externalBook}
                  onChange={(e) => {
                    setExternalBook(e.target.value);
                    changed();
                  }}
                />
              </label>
              <label>
                产品版本
                <input
                  disabled={busy || !data.can_configure}
                  value={version}
                  onChange={(e) => {
                    setVersion(e.target.value);
                    changed();
                  }}
                />
              </label>
              <label>
                部署方式
                <select
                  disabled={busy || !data.can_configure}
                  value={deployment}
                  onChange={(e) => {
                    setDeployment(e.target.value as Config['deployment']);
                    changed();
                  }}
                >
                  <option value="unknown">待确认</option>
                  <option value="on_premise">本地或云主机部署</option>
                  <option value="tplus_cloud">T+Cloud</option>
                </select>
              </label>
            </div>
            <div className="integration-fields">
              <label>
                科目映射 · 每行“本地科目,外部科目”
                <textarea
                  rows={5}
                  disabled={busy || !data.can_configure}
                  value={mappings}
                  placeholder={'1002,1002\n6602.02,6602.02'}
                  onChange={(e) => {
                    setMappings(e.target.value);
                    changed();
                  }}
                />
              </label>
              <label>
                辅助项映射 · 每行“类型,本地值,外部编号”
                <textarea
                  rows={5}
                  disabled={busy || !data.can_configure}
                  value={auxiliaries}
                  placeholder={
                    'department,部门ID,外部部门编号\ncounterparty,原始往来名称,外部往来编号'
                  }
                  onChange={(e) => {
                    setAuxiliaries(e.target.value);
                    changed();
                  }}
                />
                <small>
                  类型：department 部门、employee 人员、counterparty 往来、project 项目。
                </small>
              </label>
            </div>
            <label>
              本次操作依据
              <input
                value={reason}
                maxLength={1000}
                disabled={busy || !data.can_review}
                placeholder="填写配置、导入或交付的核对依据"
                onChange={(e) => {
                  setReason(e.target.value);
                  changed();
                }}
              />
            </label>
            <button
              className="button secondary"
              disabled={
                busy ||
                !data.can_configure ||
                !externalBook.trim() ||
                !mappings.trim() ||
                !reason.trim()
              }
              onClick={() => void configure()}
            >
              保存新映射版本
            </button>
            {!data.can_configure && <p>映射由有明确长期账套管理授权的会计维护。</p>}
          </section>
          <section className="integration-section">
            <div className="integration-section-title">
              <span>02</span>
              <h2>保留客户导出资料</h2>
            </div>
            <p>
              科目、往来、项目、银行账户、凭证与余额按来源编号保留。中性模板需按客户实际导出文件转换；导入的凭证与余额只作核对参考。
            </p>
            <div className="integration-actions">
              <label className="integration-vendor">
                目标软件
                <select
                  aria-label="选择目标软件"
                  value={vendor}
                  disabled={busy || !data.can_review || !data.configuration}
                  onChange={(e) => {
                    setVendor(e.target.value);
                    changed();
                  }}
                >
                  {VENDOR_TEMPLATES.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button secondary"
                disabled={busy || !data.can_review || !data.configuration}
                onClick={() =>
                  void run(async () => {
                    const choice =
                      VENDOR_TEMPLATES.find((item) => item.id === vendor) ?? VENDOR_TEMPLATES[0];
                    const query =
                      vendor === 'neutral' ? '' : `?vendor=${encodeURIComponent(vendor)}`;
                    const value = await api(`${base}/template${query}`, session);
                    if (live.current) download(value, choice.filename);
                  })
                }
              >
                <Download size={15} />
                {vendor === 'neutral' ? '下载中性模板' : '下载转换参考'}
              </button>
              <label className="integration-file">
                <FileUp size={15} />
                <input
                  type="file"
                  accept=".json,.csv"
                  disabled={busy || !data.can_review}
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    changed();
                  }}
                />
              </label>
              <button
                className="button primary"
                disabled={
                  busy || !data.can_review || !data.configuration || !file || !reason.trim()
                }
                onClick={() => void importFile()}
              >
                预览导入
              </button>
            </div>
            {vendor !== 'neutral' && (
              <p className="field-help">
                转换参考给出该软件导出列与中性字段的对照；请先按对照整理成中性模板，再“预览导入”。
              </p>
            )}
            {preview && (
              <div className="integration-preview">
                <h3>
                  共 {preview.record_count} 条 · 重复 {preview.duplicates} · 冲突{' '}
                  {preview.conflicts}
                </h3>
                <ul>
                  {preview.records.slice(0, 20).map((r) => (
                    <li key={`${r.kind}:${r.external_id}`}>
                      {r.external_id} · {r.name || r.kind} · {r.period}
                    </li>
                  ))}
                </ul>
                {preview.record_count > 20 && <p>此处显示前 20 条，确认时校验完整文件。</p>}
                <button
                  className="button primary"
                  disabled={
                    busy ||
                    !data.can_review ||
                    preview.conflicts > 0 ||
                    preview.state === 'confirmed'
                  }
                  onClick={() =>
                    void run(async () => {
                      const value = await api<Preview>(
                        `${base}/imports/${preview.id}/confirm`,
                        session,
                        { method: 'POST', body: { fingerprint: preview.fingerprint } },
                      );
                      if (live.current) {
                        setPreview(value);
                        await load();
                        setMessage('原始参考记录已保留，没有生成重复凭证。');
                      }
                    })
                  }
                >
                  {preview.state === 'confirmed' ? '已确认保留' : '确认保留参考记录'}
                </button>
              </div>
            )}
            {data.imports.map((row) => (
              <button
                className="integration-history"
                key={row.id}
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const value = await api<Preview>(`${base}/imports/${row.id}`, session);
                    if (live.current) setPreview(value);
                  })
                }
              >
                {row.record_count} 条参考记录{' '}
                <span>{row.state === 'confirmed' ? '已保留' : '待确认'}</span>
              </button>
            ))}
          </section>
          <section className="integration-section">
            <div className="integration-section-title">
              <span>03</span>
              <h2>准备已审核凭证交付包</h2>
            </div>
            <p>
              每个导出只创建一个持久意图，科目与辅助项映射随包冻结。当前包为中性
              JSON，尚未验证为客户 T+
              的原生导入格式。导入后请按原导出的回执模板核对本地科目与辅助项，并保留客户软件的外部凭证号。
            </p>
            <div className="integration-actions">
              <select
                aria-label="选择已审核导出"
                disabled={busy || !data.can_review}
                value={exportId}
                onChange={(e) => {
                  setExportId(e.target.value);
                  keys.current.export = '';
                }}
              >
                <option value="">选择已审核导出</option>
                {exports.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.filename}
                  </option>
                ))}
              </select>
              <button
                className="button primary"
                disabled={
                  busy || !data.can_review || !exportId || !data.configuration || !reason.trim()
                }
                onClick={() => void prepare()}
              >
                冻结并下载交付包
              </button>
            </div>
            {data.outbox.map((row) => (
              <div className="integration-history" key={row.id}>
                <span>{row.external_code.slice(0, 12)} · 等待版本适配</span>
                <button
                  className="button secondary small"
                  disabled={busy || !data.can_review}
                  onClick={() => void makeArtifact(row.id)}
                >
                  生成 54 列
                </button>
                <button
                  className="button secondary small"
                  disabled={busy || !data.can_review}
                  onClick={() => void loadArtifact(row.id)}
                >
                  查看适配文件
                </button>
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const intent = await api<Intent>(`${base}/outbox/${row.id}`, session);
                      if (live.current) download(intent.package, `tplus-package-${intent.id}.json`);
                    })
                  }
                >
                  下载原包
                </button>
              </div>
            ))}
          </section>
          <section className="integration-section">
            <div className="integration-section-title">
              <span>04</span>
              <h2>54 列合成适配与客户回执</h2>
            </div>
            <p>
              仅从已复核冻结的交付包生成；54 列为合成协议，不是客户 T+
              原生模板。下载文件交给客户软件导入后，再逐行登记回执；“回执一致”不代表
              T+ 已独立过账，外发仍关闭。
            </p>
            {!artifact ? (
              <p>从上方交付意图选择“生成 54 列”或“查看适配文件”。</p>
            ) : (
              <div className="integration-artifact">
                <div className="integration-history">
                  <span>
                    {artifact.filename} · {artifact.row_count} 行 · {artifact.voucher_count} 张凭证
                  </span>
                  <span className="integration-offline">合成 · 非原生 T+</span>
                  <button
                    className="button secondary small"
                    disabled={busy}
                    onClick={() => void downloadArtifact()}
                  >
                    <Download size={14} /> 下载 CSV
                  </button>
                </div>
                <p className="field-help">
                  内容指纹 {artifact.content_sha256.slice(0, 16)}… · 仅登记客户文件结果
                </p>
                <div className="integration-fields">
                  <label>
                    客户回执编号
                    <input
                      value={receiptCode}
                      maxLength={120}
                      disabled={busy}
                      placeholder="例如 TPLUS-R-001"
                      onChange={(e) => setReceiptCode(e.target.value)}
                    />
                  </label>
                  <label>
                    逐行回执 CSV
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      disabled={busy}
                      onChange={(e) => setReceiptFile(e.target.files?.[0] ?? null)}
                    />
                    <small>固定四列：行号,外部凭证编号,结果,原因。</small>
                  </label>
                </div>
                <button
                  className="button primary"
                  disabled={busy || !receiptCode.trim() || !receiptFile || !reason.trim()}
                  onClick={() => void submitReceipt()}
                >
                  <Upload size={15} /> 登记回执
                </button>
                {receipts.length > 0 && (
                  <div className="integration-receipts">
                    <h3>回执记录</h3>
                    {receipts.map((row) => (
                      <div className="integration-history" key={row.id}>
                        <span>
                          {row.receipt_code} · {row.result_status} · 接受 {row.accepted_count} / 拒绝{' '}
                          {row.rejected_count} / 重复 {row.duplicate_count} / 未知 {row.unknown_count}
                        </span>
                        <span className="integration-offline">
                          <FileCheck2 size={14} /> 外部过账未确认
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>
          {data.pagination.total > 20 && (
            <div className="integration-actions">
              <button
                className="button secondary"
                disabled={busy || offset === 0}
                onClick={() => setOffset((x) => Math.max(0, x - 20))}
              >
                上一页
              </button>
              <span>第 {Math.floor(offset / 20) + 1} 页</span>
              <button
                className="button secondary"
                disabled={busy || !data.pagination.has_more}
                onClick={() => setOffset((x) => x + 20)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </Wrapper>
  );
}
