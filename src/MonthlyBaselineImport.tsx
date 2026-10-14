import { useEffect, useMemo, useRef, useState } from 'react';
import { FileUp, LoaderCircle } from 'lucide-react';
import { errorMessage } from './api';
import { downloadCsv } from './csv';
import { monthlyImportSummary, previousMonthlyPeriod } from './monthly-helpers';
import type { Session } from './types';
import { uploadSizeError } from './transport-limits';
import { useMonthlyCommand } from './useMonthlyCommand';

type Props = {
  session: Session;
  bookId: string;
  period: string;
  canEdit: boolean;
  revoked: boolean;
  externalBusy: boolean;
  load: () => Promise<void>;
  onBusyChange: (busy: boolean) => void;
  onDirtyChange: (dirty: boolean) => void;
  onChanged?: () => void;
};
export function MonthlyBaselineImport({
  session,
  bookId,
  period,
  canEdit,
  revoked,
  externalBusy,
  load,
  onBusyChange,
  onDirtyChange,
  onChanged,
}: Props) {
  const [kind, setKind] = useState<'opening' | 'closing'>('opening'),
    [file, setFile] = useState<{ name: string; format: 'json' | 'csv'; content: string } | null>(
      null,
    );
  const [source, setSource] = useState(''),
    [evidence, setEvidence] = useState(''),
    [reason, setReason] = useState(''),
    [confirmed, setConfirmed] = useState(false);
  const [readBusy, setReadBusy] = useState(false),
    [fileError, setFileError] = useState('');
  const command = useMonthlyCommand(session, bookId, { revoked, load, onChanged });
  const active = useRef(true),
    epoch = useRef(0),
    input = useRef<HTMLInputElement>(null);
  const dirty = !!file || !!source || !!evidence || !!reason;
  useEffect(() => {
    onBusyChange(readBusy || command.busy);
    return () => onBusyChange(false);
  }, [readBusy, command.busy, onBusyChange]);
  useEffect(() => {
    onDirtyChange(dirty && !revoked);
    return () => onDirtyChange(false);
  }, [dirty, revoked, onDirtyChange]);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    if (revoked) {
      epoch.current++;
      setFile(null);
      setSource('');
      setEvidence('');
      setReason('');
      setConfirmed(false);
      setReadBusy(false);
      setFileError('');
    }
  }, [revoked]);
  const locked = externalBusy || readBusy || command.busy || revoked || !canEdit;
  const expected = kind === 'opening' ? previousMonthlyPeriod(period) : period;
  const { summary, issue } = useMemo(() => {
    if (!file) return { summary: null, issue: '' };
    try {
      return { summary: monthlyImportSummary(file.content, file.format, expected), issue: '' };
    } catch (failure) {
      return { summary: null, issue: errorMessage(failure) };
    }
  }, [file, expected]);
  async function readFile(selected?: File) {
    if (!selected || locked) return;
    const stamp = ++epoch.current;
    setReadBusy(true);
    setFile(null);
    setFileError('');
    setConfirmed(false);
    try {
      const invalid = uploadSizeError(selected.size, 2_000_000);
      if (invalid) throw new Error(`余额文件${invalid}`);
      const extension = selected.name.split('.').pop()?.toLowerCase();
      if (extension !== 'csv' && extension !== 'json')
        throw new Error('请选择 JSON 或 CSV 余额文件');
      let content: string;
      try {
        content = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
          await selected.arrayBuffer(),
        );
      } catch {
        throw new Error('余额文件须使用有效 UTF-8 编码，请按模板重新导出');
      }
      if (active.current && stamp === epoch.current)
        setFile({ name: selected.name, format: extension, content });
    } catch (failure) {
      if (active.current && stamp === epoch.current) setFileError(errorMessage(failure));
    } finally {
      if (active.current && stamp === epoch.current) setReadBusy(false);
    }
  }
  function clear() {
    setFile(null);
    setSource('');
    setEvidence('');
    setReason('');
    setConfirmed(false);
    if (input.current) input.current.value = '';
  }
  function template() {
    downloadCsv(
      `account_code,balance,auxiliary_values,period,currency,scope\n填写实际科目,0.00,{},${expected},CNY,posted\n`,
      `余额模板-${expected}.csv`,
    );
  }
  return (
    <details className="monthly-baseline-import">
      <summary>导入结构化余额文件 · JSON / CSV</summary>
      <p className="monthly-inline-note">
        按模板整理外部已过账余额；上传原文留存并可下载。T+
        等软件原始格式应先完成字段映射，不能直接混入未过账记录。
      </p>
      {(fileError || issue || command.error) && (
        <p className="inline-error" role="alert">
          {fileError || issue || command.error}
        </p>
      )}
      {command.notice && (
        <p className="monthly-inline-note" role="status">
          {command.notice}
        </p>
      )}
      <div className="monthly-form">
        <label>
          导入余额类型
          <select
            aria-label="余额文件类型"
            value={kind}
            disabled={locked}
            onChange={(event) => {
              setKind(event.target.value as typeof kind);
              setConfirmed(false);
            }}
          >
            <option value="opening">期初（上月末）</option>
            <option value="closing">期末（本月末）</option>
          </select>
        </label>
        <label>
          选择文件
          <input
            ref={input}
            aria-label="外部余额文件"
            type="file"
            accept=".json,.csv,application/json,text/csv"
            disabled={locked}
            onChange={(event) => void readFile(event.target.files?.[0])}
          />
        </label>
        <label>
          财务软件 / 导出文件编号
          <input
            aria-label="余额文件来源"
            value={source}
            maxLength={500}
            disabled={locked}
            onChange={(event) => setSource(event.target.value)}
          />
        </label>
        <label>
          原件资料依据
          <input
            aria-label="余额文件原件依据"
            value={evidence}
            maxLength={500}
            disabled={locked}
            onChange={(event) => setEvidence(event.target.value)}
          />
        </label>
        <label className="monthly-wide">
          整理与导入依据
          <input
            aria-label="余额文件导入依据"
            value={reason}
            maxLength={1000}
            disabled={locked}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      </div>
      {summary && (
        <p className="monthly-inline-note">
          {file!.name} · {summary.period} · {summary.rows} 项，人民币，外部已过账余额范围。
        </p>
      )}
      <label className="monthly-confirm">
        <input
          aria-label="确认余额文件月份币种与过账范围"
          type="checkbox"
          checked={confirmed}
          disabled={locked || !summary}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        我已核对原始余额文件、月份、币种及过账范围。
      </label>
      <div className="monthly-actions">
        <button className="button secondary small" disabled={locked} onClick={template}>
          下载余额模板
        </button>
        <button
          className="button primary small"
          disabled={
            locked ||
            !summary ||
            !confirmed ||
            !source.trim() ||
            !evidence.trim() ||
            reason.trim().length < 3
          }
          onClick={() =>
            file &&
            void command.run(
              '/baselines/import',
              {
                kind,
                format: file.format,
                filename: file.name,
                content: file.content,
                external_reference: source.trim(),
                evidence_ref: evidence.trim(),
                reason: reason.trim(),
              },
              '余额文件已保存，等待另一位会计独立确认。',
              clear,
              true,
            )
          }
        >
          {readBusy || command.busy ? (
            <LoaderCircle className="spinner" size={14} />
          ) : (
            <FileUp size={14} />
          )}
          导入并等待独立确认
        </button>
        <button className="button secondary small" disabled={locked || !dirty} onClick={clear}>
          放弃本次文件导入
        </button>
      </div>
    </details>
  );
}
