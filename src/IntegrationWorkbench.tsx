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
  outbox: {
    id: string;
    export_id: string;
    state: string;
    external_code: string;
    statuses: {
      exported: { at: string | null; evidence: string | null };
      received: { at: string | null; evidence: string | null };
      posted: { at: string | null; evidence: string | null };
    };
    manual_posted_claim?: {
      at: string;
      evidence: string;
      evidence_attached: boolean;
      filename: string | null;
      declared_by: string;
      declared_by_name: string;
    } | null;
    result_summary: 'not_exported' | 'result_unknown' | 'received' | 'partial' | 'posted';
    partial_failure: boolean;
    result_unknown: boolean;
    mapping_changed?: boolean;
    mapping_version?: number;
    current_mapping_version?: number | null;
    confirmation_required?: boolean;
  }[];
  formal_book: {
    system: 'unassessed' | 'local' | 'external';
    name: string;
    policy_version: number | null;
    approved: boolean;
  };
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
type ReturnRow = {
  voucher_code: string;
  external_code: string;
  status: string;
  reason: string;
  posted_date: string | null;
  expected_cents: number;
  actual_cents: number | null;
  expected_accounts: string[];
  actual_accounts: string[];
  voided_after_export?: boolean;
  handled?: boolean;
  handler?: {
    action: string;
    reason: string;
    evidence_ref: string;
    handled_by: string;
    handled_at: string;
  } | null;
};
type ReturnImport = {
  id: string;
  batch_code: string;
  source_kind?: 'file' | 'manual';
  result_status: 'accepted' | 'partial';
  received: boolean;
  posted: boolean;
  posted_declared?: boolean;
  manual_posted_claim?: boolean;
  evidence_attached?: boolean;
  verification_state?: string;
  verification_label?: string;
  evidence_attachment?: {
    filename: string;
    sha256: string;
    uploaded_by_name: string;
    uploaded_at: string;
  } | null;
  summary_breakdown?: {
    confirmed_count: number;
    pending_count: number;
    handled_count: number;
  };
  created_by_name?: string;
  row_count: number;
  counts: {
    accepted: number;
    missing: number;
    extra: number;
    mismatch: number;
    discrepancy: number;
  };
  created_at: string;
  rows: ReturnRow[];
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

async function fileBase64(file: File): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('无法读取证据原件'));
    reader.readAsDataURL(file);
  });
  return dataUrl.split(',', 2)[1] || '';
}

export function IntegrationWorkbench({
  session,
  bookId,
  focus = '',
  onBack,
  onBusyChange,
  embedded = false,
}: {
  session: Session;
  bookId: string;
  focus?: string;
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
    [returns, setReturns] = useState<ReturnImport[]>([]),
    [returnFile, setReturnFile] = useState<File | null>(null),
    [handleDraft, setHandleDraft] = useState<{
      importId: string;
      voucherCode: string;
      action: 'resolved' | 'ignored';
      reasonCode:
        'amount_mismatch' | 'account_mismatch' | 'date_mismatch' | 'voucher_missing' | 'other';
      reason: string;
      evidence: string;
    } | null>(null),
    [manualEvidence, setManualEvidence] = useState<File | null>(null),
    [manualReturn, setManualReturn] = useState({
      received: true,
      posted: false,
      postedDate: '',
      evidence: '',
      note: '',
    }),
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
  async function refreshArtifact(outboxId: string) {
    const value = await api<{ artifacts: Artifact[] }>(
      `${base}/outbox/${outboxId}/artifacts`,
      session,
    );
    if (!live.current) return;
    setArtifact(value.artifacts[0] ?? null);
    setReceipts([]);
    setReturns([]);
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
      const returned = await api<{ returns: ReturnImport[] }>(
        `${base}/artifacts/${value.artifacts[0].id}/returns`,
        session,
      );
      if (live.current) setReturns(returned.returns);
    }
  }
  async function openFocusTask(ref: string) {
    await run(async () => {
      const listed = await api<{
        tasks: {
          object_id: string;
          import_id: string;
          voucher_code: string;
          outbox_id: string;
        }[];
      }>(`${base}/returns/tasks?limit=200`, session);
      if (!live.current) return;
      const task = listed.tasks.find((item) => item.object_id === ref);
      if (!task) {
        setMessage('该差异已处置或不在当前待办中，请刷新待办后重试。');
        return;
      }
      await refreshArtifact(task.outbox_id);
      if (!live.current) return;
      const inferReasonCode = (
        status?: string,
      ): 'amount_mismatch' | 'account_mismatch' | 'date_mismatch' | 'voucher_missing' | 'other' => {
        if (status === 'amount_mismatch') return 'amount_mismatch';
        if (status === 'account_mismatch') return 'account_mismatch';
        if (status === 'missing') return 'voucher_missing';
        return 'other';
      };
      setHandleDraft({
        importId: task.import_id,
        voucherCode: task.voucher_code,
        action: 'resolved',
        reasonCode: inferReasonCode((task as { status?: string }).status),
        reason: '',
        evidence: '',
      });
      setMessage('已定位返回文件差异，请核对金额、科目和过账日期，再填写处置原因与证据。');
    });
  }
  useEffect(() => {
    if (focus) void openFocusTask(focus);
    // 待办跳转只传一次目标；处置完成后不自动重放。
  }, [focus]);
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
    await run(() => refreshArtifact(outboxId));
  }
  async function makeArtifact(outboxId: string) {
    await run(async () => {
      const value = await api<Artifact>(`${base}/outbox/${outboxId}/artifacts`, session, {
        method: 'POST',
      });
      if (!live.current) return;
      setArtifact(value);
      setReceipts([]);
      setReturns([]);
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
      await load();
      setMessage(
        `回执已登记：接受 ${value.accepted_count}、拒绝 ${value.rejected_count}、重复 ${value.duplicate_count}、未知 ${value.unknown_count}。外部过账仍未确认。`,
      );
    });
  }
  async function submitRowHandle(event: React.FormEvent) {
    event.preventDefault();
    if (!artifact || !handleDraft) return;
    await run(async () => {
      const value = await api<ReturnImport>(
        `${base}/artifacts/${artifact.id}/returns/${handleDraft.importId}/rows/${encodeURIComponent(handleDraft.voucherCode)}/handle`,
        session,
        {
          method: 'POST',
          body: {
            action: handleDraft.action,
            reason_code: handleDraft.reasonCode,
            reason: handleDraft.reason,
            evidence_ref: handleDraft.evidence,
          },
        },
      );
      setReturns((rows) => rows.map((row) => (row.id === value.id ? value : row)));
      setHandleDraft(null);
      setMessage('差异已处置，原返回行与处置记录同时保留。');
    });
  }

  async function submitManualReturn() {
    if (!artifact) return;
    if (!manualEvidence) {
      setMessage('手工登记必须上传证据原件（PDF/PNG/JPEG，≤2MB）。');
      return;
    }
    if (uploadSizeError(manualEvidence.size, 2_000_000)) {
      setMessage('证据原件须在 2MB 以内。');
      return;
    }
    const intent = data?.outbox.find((row) => row.id === artifact.outbox_id);
    if (!intent?.external_code) return;
    await run(async () => {
      const value = await api<ReturnImport>(
        `${base}/artifacts/${artifact.id}/returns/manual`,
        session,
        {
          method: 'POST',
          body: {
            batch_code: intent.external_code,
            received: manualReturn.received,
            posted: manualReturn.posted,
            posted_date: manualReturn.postedDate,
            evidence_ref: manualReturn.evidence,
            note: manualReturn.note,
          },
        },
      );
      const base64 = await fileBase64(manualEvidence);
      await api(`${base}/artifacts/${artifact.id}/returns/${value.id}/evidence`, session, {
        method: 'POST',
        body: {
          filename: manualEvidence.name,
          media_type: manualEvidence.type || 'application/octet-stream',
          content_base64: base64,
        },
      });
      const refreshed = await api<{ returns: ReturnImport[] }>(
        `${base}/artifacts/${artifact.id}/returns`,
        session,
      );
      setReturns(refreshed.returns);
      setManualReturn({ received: true, posted: false, postedDate: '', evidence: '', note: '' });
      setManualEvidence(null);
      setMessage('手工回执与证据原件已登记；操作人已留痕。');
    });
  }

  async function submitReturn() {
    if (!artifact || !returnFile) return;
    const intent = data?.outbox.find((row) => row.id === artifact.outbox_id);
    if (!intent?.external_code) throw new Error('当前交付意图缺少批次标识');
    await run(async () => {
      if (uploadSizeError(returnFile.size, 2_000_000))
        throw new Error(uploadSizeError(returnFile.size, 2_000_000) ?? '返回文件过大');
      const content = await returnFile.text();
      const value = await api<ReturnImport>(`${base}/artifacts/${artifact.id}/returns`, session, {
        method: 'POST',
        body: { batch_code: intent.external_code, content },
      });
      if (!live.current) return;
      setReturns((rows) => [value, ...rows.filter((row) => row.id !== value.id)]);
      setReturnFile(null);
      await load();
      setMessage(
        value.posted
          ? `返回文件已核对：${value.counts.accepted} 张已接收并过账。`
          : value.received
            ? `返回文件已接收：${value.counts.accepted} 张，尚缺逐笔过账日期。`
            : `返回文件存在差异：缺 ${value.counts.missing}、多 ${value.counts.extra}、金额/科目 ${value.counts.mismatch}，未接收。`,
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
            <p className="integration-formal-book">
              正式账：
              {data.formal_book.system === 'external'
                ? `外部系统 ${data.formal_book.name || '未命名'}`
                : data.formal_book.system === 'local'
                  ? '本系统'
                  : '未核定（不能生成正式报表）'}
              {data.formal_book.policy_version
                ? ` · 政策 V${data.formal_book.policy_version}${data.formal_book.approved ? ' · 已批准' : ' · 未批准'}`
                : ' · 缺少生效政策'}
            </p>
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
                <span>{row.external_code.slice(0, 12)}</span>
                <span
                  className={`integration-state ${row.statuses.exported.at ? 'done' : 'pending'}`}
                >
                  {row.statuses.exported.at
                    ? `已导出 · ${row.statuses.exported.at.slice(0, 10)}`
                    : '未导出'}
                </span>
                <span
                  className={`integration-state ${row.statuses.received.at ? 'done' : 'pending'}`}
                >
                  {row.statuses.received.at
                    ? `对方已接收 · ${row.statuses.received.at.slice(0, 10)}`
                    : '未接收'}
                </span>
                <span
                  className={`integration-state ${row.statuses.posted.at ? 'done' : 'pending'}`}
                >
                  {row.statuses.posted.at
                    ? `对方已过账 · ${row.statuses.posted.at.slice(0, 10)}`
                    : '未过账'}
                </span>
                {!row.statuses.posted.at && row.manual_posted_claim && (
                  <span className="integration-offline">
                    人工声明已过账 ·{' '}
                    {row.manual_posted_claim.evidence_attached
                      ? `已附原件 ${row.manual_posted_claim.filename || ''}（未逐笔核对）`
                      : '缺证据原件'}
                    · {row.manual_posted_claim.declared_by_name}
                  </span>
                )}
                {row.result_unknown && (
                  <span className="integration-offline">尚无对方结果，不自动重发</span>
                )}
                {row.mapping_changed && (
                  <span className="integration-offline">
                    映射已变更（导出用 v{row.mapping_version}，当前 v{row.current_mapping_version}）
                    {row.confirmation_required
                      ? '，需确认后重新导出，不能直接登记接收'
                      : '，已接收批次不受影响'}
                  </span>
                )}
                {row.partial_failure && (
                  <span className="integration-offline">部分失败，请逐笔核对</span>
                )}
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
              原生模板。下载文件交给客户软件导入后，再逐行登记回执；“回执一致”不代表 T+
              已独立过账，外发仍关闭。
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
                          {row.receipt_code} · {row.result_status} · 接受 {row.accepted_count} /
                          拒绝 {row.rejected_count} / 重复 {row.duplicate_count} / 未知{' '}
                          {row.unknown_count}
                        </span>
                        <span className="integration-offline">
                          <FileCheck2 size={14} /> 外部过账未确认
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="integration-returns">
                  <h3>返回文件逐笔核对</h3>
                  <label>
                    客户返回文件 CSV
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      disabled={busy}
                      onChange={(e) => setReturnFile(e.target.files?.[0] ?? null)}
                    />
                    <small>
                      八列契约：批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目。
                    </small>
                  </label>
                  <button
                    className="button secondary"
                    disabled={busy || !returnFile}
                    onClick={() => void submitReturn()}
                  >
                    <Upload size={15} /> 核对返回文件
                  </button>
                  <details className="integration-manual-return">
                    <summary>客户软件无文件时，手工登记接收/过账</summary>
                    <div className="integration-form">
                      <label>
                        已接收
                        <input
                          type="checkbox"
                          checked={manualReturn.received}
                          onChange={(e) =>
                            setManualReturn((d) => ({ ...d, received: e.target.checked }))
                          }
                        />
                      </label>
                      <label>
                        已过账
                        <input
                          type="checkbox"
                          checked={manualReturn.posted}
                          disabled={!manualReturn.received}
                          onChange={(e) =>
                            setManualReturn((d) => ({ ...d, posted: e.target.checked }))
                          }
                        />
                      </label>
                      <label>
                        对方过账日期
                        <input
                          type="date"
                          value={manualReturn.postedDate}
                          disabled={!manualReturn.posted}
                          onChange={(e) =>
                            setManualReturn((d) => ({ ...d, postedDate: e.target.value }))
                          }
                        />
                      </label>
                      <label>
                        证据引用
                        <input
                          value={manualReturn.evidence}
                          placeholder="客户短信/邮件/截图归档编号"
                          onChange={(e) =>
                            setManualReturn((d) => ({ ...d, evidence: e.target.value }))
                          }
                        />
                      </label>
                      <label>
                        备注
                        <input
                          value={manualReturn.note}
                          onChange={(e) => setManualReturn((d) => ({ ...d, note: e.target.value }))}
                        />
                      </label>
                      <label>
                        证据原件
                        <input
                          type="file"
                          accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                          disabled={busy}
                          onChange={(e) => setManualEvidence(e.target.files?.[0] ?? null)}
                        />
                        <small>PDF/PNG/JPEG，≤2MB；与证据引用一并留痕。</small>
                      </label>
                    </div>
                    <button
                      className="button secondary"
                      disabled={
                        busy ||
                        manualReturn.evidence.trim().length < 5 ||
                        !manualEvidence ||
                        (manualReturn.posted && !manualReturn.postedDate)
                      }
                      onClick={() => void submitManualReturn()}
                    >
                      登记手工回执
                    </button>
                  </details>
                  {returns.length > 0 && (
                    <div className="integration-receipts">
                      {returns.map((row) => (
                        <div className="integration-history" key={row.id}>
                          <span>
                            {row.batch_code} ·{' '}
                            <strong
                              className="integration-receipt-tier"
                              data-tier={
                                row.posted
                                  ? 'posted'
                                  : row.verification_state === 'file_partial_discrepancy' ||
                                      row.verification_state ===
                                        'manual_posted_claimed_with_evidence'
                                    ? 'pending'
                                    : 'received'
                              }
                            >
                              {row.verification_label ?? row.result_status}
                            </strong>{' '}
                            ·{' '}
                            {row.source_kind === 'manual'
                              ? `${row.received ? '已接收' : '未接收'} · ${row.posted ? '已核实过账' : row.manual_posted_claim ? '人工声明已过账（未逐笔核对）' : '未过账'}`
                              : `已确认 ${row.summary_breakdown?.confirmed_count ?? row.counts.accepted} / 待查 ${row.summary_breakdown?.pending_count ?? row.counts.discrepancy} / 已处置 ${row.summary_breakdown?.handled_count ?? 0} · ${row.received ? '已接收' : '未接收'} · ${row.posted ? '已核实过账' : '未核实过账'}`}
                          </span>
                          <span className="integration-offline">
                            {row.created_by_name ? `登记人 ${row.created_by_name} · ` : ''}
                            {row.evidence_attachment
                              ? `原件 ${row.evidence_attachment.filename}（${row.evidence_attachment.sha256.slice(0, 8)}…）`
                              : `缺 ${row.counts.missing} / 多 ${row.counts.extra} / 金额科目 ${row.counts.mismatch}`}
                          </span>
                          <details
                            className="integration-return-rows"
                            open={handleDraft?.importId === row.id}
                          >
                            <summary>逐笔明细（{row.rows.length}）</summary>
                            <table>
                              <thead>
                                <tr>
                                  <th>凭证标识</th>
                                  <th>结果</th>
                                  <th>金额</th>
                                  <th>过账日期</th>
                                  <th>原因</th>
                                  <th>处置</th>
                                </tr>
                              </thead>
                              <tbody>
                                {row.rows.map((item) => (
                                  <tr key={item.voucher_code}>
                                    <td data-label="凭证标识">{item.voucher_code}</td>
                                    <td data-label="结果">{item.status}</td>
                                    <td data-label="金额">
                                      {item.actual_cents == null
                                        ? '缺金额'
                                        : `¥${(item.actual_cents / 100).toFixed(2)}`}
                                    </td>
                                    <td data-label="过账日期">{item.posted_date ?? '未提供'}</td>
                                    <td data-label="原因">
                                      {item.reason || '—'}
                                      {item.voided_after_export ? (
                                        <span className="integration-offline">
                                          导出后已冲销，须按冲销后的凭证重新核对
                                        </span>
                                      ) : null}
                                    </td>
                                    <td data-label="处置">
                                      {item.handled ? (
                                        <span>
                                          已处置 ·{' '}
                                          {item.handler?.action === 'ignored' ? '忽略' : '已解决'}
                                        </span>
                                      ) : item.status === 'accepted' ? (
                                        '—'
                                      ) : (
                                        <button
                                          type="button"
                                          className="button secondary"
                                          disabled={busy}
                                          onClick={() =>
                                            setHandleDraft({
                                              importId: row.id,
                                              voucherCode: item.voucher_code,
                                              action: 'resolved',
                                              reasonCode:
                                                item.status === 'amount_mismatch'
                                                  ? 'amount_mismatch'
                                                  : item.status === 'account_mismatch'
                                                    ? 'account_mismatch'
                                                    : item.status === 'missing'
                                                      ? 'voucher_missing'
                                                      : 'other',
                                              reason: '',
                                              evidence: '',
                                            })
                                          }
                                        >
                                          处置差异
                                        </button>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            {handleDraft?.importId === row.id && (
                              <form className="integration-handle" onSubmit={submitRowHandle}>
                                <strong>处置 {handleDraft.voucherCode}</strong>
                                <label>
                                  处置方式
                                  <select
                                    value={handleDraft.action}
                                    onChange={(e) =>
                                      setHandleDraft((d) =>
                                        d
                                          ? {
                                              ...d,
                                              action: e.target.value as 'resolved' | 'ignored',
                                            }
                                          : d,
                                      )
                                    }
                                  >
                                    <option value="resolved">已解决（说明原因）</option>
                                    <option value="ignored">忽略（保留原差异）</option>
                                  </select>
                                </label>
                                <label>
                                  差异原因码
                                  <select
                                    value={handleDraft.reasonCode}
                                    onChange={(e) =>
                                      setHandleDraft((d) =>
                                        d
                                          ? {
                                              ...d,
                                              reasonCode: e.target.value as
                                                | 'amount_mismatch'
                                                | 'account_mismatch'
                                                | 'date_mismatch'
                                                | 'voucher_missing'
                                                | 'other',
                                            }
                                          : d,
                                      )
                                    }
                                  >
                                    <option value="amount_mismatch">金额不一致</option>
                                    <option value="account_mismatch">科目不一致</option>
                                    <option value="date_mismatch">过账日期不一致</option>
                                    <option value="voucher_missing">对方缺失凭证</option>
                                    <option value="other">其他原因</option>
                                  </select>
                                </label>
                                <label>
                                  原因
                                  <input
                                    value={handleDraft.reason}
                                    minLength={3}
                                    required
                                    onChange={(e) =>
                                      setHandleDraft((d) =>
                                        d ? { ...d, reason: e.target.value } : d,
                                      )
                                    }
                                  />
                                </label>
                                <label>
                                  证据
                                  <input
                                    value={handleDraft.evidence}
                                    minLength={5}
                                    required
                                    onChange={(e) =>
                                      setHandleDraft((d) =>
                                        d ? { ...d, evidence: e.target.value } : d,
                                      )
                                    }
                                  />
                                </label>
                                <div className="integration-actions">
                                  <button className="button primary" disabled={busy} type="submit">
                                    保存处置
                                  </button>
                                  <button
                                    className="button secondary"
                                    type="button"
                                    onClick={() => setHandleDraft(null)}
                                  >
                                    取消
                                  </button>
                                </div>
                              </form>
                            )}
                          </details>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
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
