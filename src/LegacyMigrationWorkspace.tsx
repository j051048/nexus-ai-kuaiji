import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Download, FileArchive, FileCheck2, FileSpreadsheet, FolderClock, History, LoaderCircle, LockKeyhole, Plus, RefreshCw, ShieldCheck, TriangleAlert, Upload, X } from 'lucide-react';
import { ApiError, api, apiBlob, errorMessage } from './api';
import type { Session } from './types';
import type { MigrationAccountMapping, MigrationAuxMapping, MigrationCategory, MigrationColumnMapping, MigrationDetail, MigrationFile, MigrationIssue, MigrationIssues, MigrationJob, MigrationList, MigrationProduct, MigrationProject, MigrationRecord, MigrationRecordKind, MigrationRecords } from './legacy-migration-types';
import { MIGRATION_CATEGORIES, MIGRATION_PRODUCTS, MIGRATION_RECORD_KINDS, MIGRATION_STEPS, migrationAcceptanceCurrent, migrationActive, migrationArchiveOnlyAccount, migrationColumnError, migrationDateError, migrationFields, migrationFrozen, migrationMoney, migrationNeedsColumnConfirmation, migrationPostingMappings, migrationReadableFields, migrationReadableLines, migrationState, migrationStep, monthBoundary, normalizedMigrationDetail, normalizedMigrationProject, parseCsvHeaders, stableMigrationKey, suggestMigrationColumns } from './legacy-migration-helpers';
import './legacy-migration.css';
import { LegacyMigrationLearning } from './LegacyMigrationLearning';
import { LegacyMigrationSettlements } from './LegacyMigrationSettlements';
import { uploadLimitLabel, uploadSizeError } from './transport-limits';

const PAGE_SIZE = 20;
const encoded = encodeURIComponent;
function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
}
function recordData(record: MigrationRecord): Record<string, unknown> { return record.data ?? record.payload ?? record; }
function recordCode(record: MigrationRecord): string { const data = recordData(record); return String(record.source_code ?? record.code ?? data.source_code ?? data.code ?? data.account_code ?? record.external_id ?? ''); }
function recordName(record: MigrationRecord): string { const data = recordData(record); return String(record.name ?? data.name ?? data.description ?? data.number ?? data.voucher_number ?? record.external_id ?? '历史记录'); }
function rawDisplay(value: unknown): string { return typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : String(value ?? '—'); }

/** Historical data stays in this archive. No route creates current journal drafts or exports. */
export function LegacyMigrationWorkspace({ session, bookId, initialProjectId, onBack, onBusyChange, onChanged, onOpenDraft }: {
  session: Session; bookId: string; initialProjectId?: string; onBack: () => void; onBusyChange?: (busy: boolean) => void; onChanged?: () => void; onOpenDraft?: (bookId: string, draftId: string) => void;
}) {
  const base = `/books/${encoded(bookId)}/legacy-migrations`;
  const [list, setList] = useState<MigrationList | null>(null);
  const [projectId, setProjectId] = useState(initialProjectId ?? '');
  const [detail, setDetail] = useState<MigrationDetail | null>(null);
  const [step, setStep] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true), [working, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [learningBusy, setLearningBusy] = useState(false), [showLearning, setShowLearning] = useState(false);
  const [settlementBusy, setSettlementBusy] = useState(false), [showSettlements, setShowSettlements] = useState(false);
  const busy = working || learningBusy || settlementBusy;
  const [listOffset, setListOffset] = useState(0);
  const [product, setProduct] = useState<MigrationProduct>('chanjet_tplus'), [productVersion, setProductVersion] = useState('');
  const [externalBook, setExternalBook] = useState(''), [sourceKey, setSourceKey] = useState('');
  const [historyStart, setHistoryStart] = useState(''), [historyEnd, setHistoryEnd] = useState(''), [createReason, setCreateReason] = useState('');
  const [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false);
  const [category, setCategory] = useState<MigrationCategory>('package'), [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [headers, setHeaders] = useState<string[]>([]), [columnMapping, setColumnMapping] = useState<MigrationColumnMapping>({ columns: {}, date_format: 'ymd', allow_grouping: false });
  const [columnsConfirmed, setColumnsConfirmed] = useState(false), [fileError, setFileError] = useState('');
  const [replaceFile, setReplaceFile] = useState('');
  const [storedMapping, setStoredMapping] = useState<{ file_id: string; filename: string; kind: MigrationRecordKind } | null>(null);
  const [recordKind, setRecordKind] = useState<MigrationRecordKind>('accounts'), [recordPeriod, setRecordPeriod] = useState(''), [recordOffset, setRecordOffset] = useState(0);
  const [records, setRecords] = useState<MigrationRecords | null>(null), [recordsLoading, setRecordsLoading] = useState(false);
  const [issueSeverity, setIssueSeverity] = useState('blocking'), [issueOffset, setIssueOffset] = useState(0), [issues, setIssues] = useState<MigrationIssues | null>(null), [issuesLoading, setIssuesLoading] = useState(false);
  const [accountEdits, setAccountEdits] = useState<Record<string, MigrationAccountMapping>>({});
  const [auxEdits, setAuxEdits] = useState<Record<string, MigrationAuxMapping>>({});
  const [settlementEdits, setSettlementEdits] = useState<{ receivable: string; payable: string } | null>(null);
  const [focus, setFocus] = useState<{ title: string; record?: MigrationRecord; issue?: MigrationIssue } | null>(null);
  const [grantUser, setGrantUser] = useState(''), [grantRole, setGrantRole] = useState<'viewer' | 'manager' | 'reviewer'>('viewer');
  const [grantReason, setGrantReason] = useState('');
  const [accessData, setAccessData] = useState<{ choices: { user_id: string; name: string; role: string }[]; grants: { user_id: string; name: string; role: string }[] } | null>(null);
  const live = useRef(true), lock = useRef(false), generation = useRef(0), detailEpoch = useRef(0), recordEpoch = useRef(0), issueEpoch = useRef(0);
  const keys = useRef<Record<string, string>>({}), input = useRef<HTMLInputElement>(null);
  const denied = useRef(false);
  const target = `${session.scope_key}:${bookId}:${projectId}`, targetRef = useRef(target); targetRef.current = target;
  const scopeRef = useRef(`${session.scope_key}:${bookId}`); scopeRef.current = `${session.scope_key}:${bookId}`;
  const dirty = Object.keys(accountEdits).length + Object.keys(auxEdits).length > 0 || settlementEdits !== null || storedMapping !== null;
  const callback = useRef(onBusyChange); callback.current = onBusyChange;
  const endpoint = `${base}/${encoded(projectId)}`;
  const frozen = !!detail && migrationFrozen(detail.project);
  const activeJobs = detail?.jobs.some(migrationActive) ?? false;
  const disabled = busy || loading;
  const canManage = !!detail?.permissions.manage && !frozen && detail.project.state !== 'cancelled' && detail.target.period_status !== 'closed';

  const clearPrivate = useCallback(() => {
    generation.current++; detailEpoch.current++; recordEpoch.current++; issueEpoch.current++;
    setList(null); setDetail(null); setRecords(null); setIssues(null); setFocus(null); setPendingFiles([]); setHeaders([]);
    setStoredMapping(null);
    setShowLearning(false);
    setShowSettlements(false);
    setAccessData(null);
    setAccountEdits({}); setAuxEdits({}); setSettlementEdits(null); setReason(''); setGrantUser(''); setGrantReason(''); setConfirmed(false); keys.current = {};
    setProductVersion(''); setExternalBook(''); setSourceKey(''); setHistoryStart(''); setHistoryEnd(''); setCreateReason('');
    setColumnMapping({ columns: {}, date_format: 'ymd', allow_grouping: false }); setColumnsConfirmed(false); setFileError(''); setReplaceFile('');
  }, []);
  useEffect(() => {
    live.current = true;
    const invalidate = (event: Event) => {
      const scope = (event as CustomEvent<string>).detail;
      if (!scope || scope === session.scope_key) { denied.current = true; clearPrivate(); setError('授权已变化，旧账资料已收起。请重新读取当前权限。'); }
    };
    window.addEventListener('accounting:forbidden', invalidate); window.addEventListener('accounting:unauthorized', invalidate);
    return () => { live.current = false; generation.current++; detailEpoch.current++; recordEpoch.current++; issueEpoch.current++; callback.current?.(false); window.removeEventListener('accounting:forbidden', invalidate); window.removeEventListener('accounting:unauthorized', invalidate); };
  }, [clearPrivate, session.scope_key]);
  useEffect(() => { callback.current?.(busy || dirty); }, [busy, dirty]);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  const loadList = useCallback(async (signal?: AbortSignal) => {
    const epoch = ++generation.current, scope = `${session.scope_key}:${bookId}`;
    setLoading(true);
    try {
      const result = await api<MigrationList>(`${base}?limit=${PAGE_SIZE}&offset=${listOffset}`, session, { signal });
      if (live.current && !signal?.aborted && generation.current === epoch && scopeRef.current === scope) { denied.current = false; setList({ ...result, projects: result.projects.map(normalizedMigrationProject) }); setError(''); }
    } catch (failure) { if (live.current && !signal?.aborted && generation.current === epoch) { setList(null); setError(errorMessage(failure)); } }
    finally { if (live.current && !signal?.aborted && generation.current === epoch) setLoading(false); }
  }, [base, bookId, listOffset, session]);
  useEffect(() => { clearPrivate(); setProjectId(initialProjectId ?? ''); setShowCreate(false); setNotice(''); }, [base, session.scope_key, initialProjectId]); // Exact task targets never fall back to the first project in a page.
  useEffect(() => { const controller = new AbortController(); void loadList(controller.signal); return () => controller.abort(); }, [loadList]);

  const loadDetail = useCallback(async (signal?: AbortSignal, initial = false) => {
    if (!projectId) return;
    const epoch = ++detailEpoch.current, commandTarget = `${session.scope_key}:${bookId}:${projectId}`;
    try {
      const value = normalizedMigrationDetail(await api<MigrationDetail>(endpoint, session, { signal }));
      if (live.current && !signal?.aborted && detailEpoch.current === epoch && targetRef.current === commandTarget) {
        setDetail(value); setConfirmed(false);
        setList(previous => previous ? { ...previous, projects: previous.projects.map(project => project.id === value.project.id ? value.project : project) } : previous);
        if (initial) setStep(migrationStep(value));
      }
    } catch (failure) { if (live.current && !signal?.aborted && detailEpoch.current === epoch && targetRef.current === commandTarget) { if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { denied.current = true; clearPrivate(); } setError(errorMessage(failure)); } }
  }, [bookId, endpoint, projectId, session]);
  useEffect(() => {
    const controller = new AbortController(); setDetail(null); setRecords(null); setIssues(null); setFocus(null); setAccessData(null); setGrantUser(''); setGrantReason(''); setShowLearning(false); setShowSettlements(false); setPendingFiles([]); setStoredMapping(null); setHeaders([]); setReplaceFile(''); setAccountEdits({}); setAuxEdits({}); setSettlementEdits(null); setReason(''); setConfirmed(false); setRecordOffset(0); setIssueOffset(0); keys.current = {};
    void loadDetail(controller.signal, true); return () => controller.abort();
  }, [loadDetail]);
  useEffect(() => {
    if (!activeJobs || dirty) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => { if (!lock.current && document.visibilityState !== 'hidden') void loadDetail(controller.signal); }, 3000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [activeJobs, dirty, loadDetail]);

  const loadRecords = useCallback(async (signal?: AbortSignal) => {
    if (!projectId || !detail?.permissions.read) return;
    const epoch = ++recordEpoch.current, commandTarget = target;
    setRecordsLoading(true);
    try {
      const result = await api<MigrationRecords>(`${endpoint}/records?kind=${encoded(MIGRATION_RECORD_KINDS[recordKind])}&limit=${PAGE_SIZE}&offset=${recordOffset}${recordPeriod ? `&period=${encoded(recordPeriod)}` : ''}`, session, { signal });
      if (live.current && !signal?.aborted && epoch === recordEpoch.current && commandTarget === targetRef.current) setRecords(result);
    } catch (failure) { if (live.current && !signal?.aborted && epoch === recordEpoch.current) { if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { denied.current = true; clearPrivate(); } else setRecords(null); setError(errorMessage(failure)); } }
    finally { if (live.current && !signal?.aborted && epoch === recordEpoch.current) setRecordsLoading(false); }
  }, [detail?.permissions.read, endpoint, projectId, recordKind, recordOffset, recordPeriod, session, target]);
  useEffect(() => { if (step !== 2 && !frozen) return; const controller = new AbortController(); setRecords(null); void loadRecords(controller.signal); return () => controller.abort(); }, [frozen, loadRecords, step]);
  const loadIssues = useCallback(async (signal?: AbortSignal) => {
    if (!projectId || !detail?.latest_validation) return;
    const epoch = ++issueEpoch.current, commandTarget = target;
    setIssuesLoading(true);
    try {
      const result = await api<MigrationIssues>(`${endpoint}/issues?limit=${PAGE_SIZE}&offset=${issueOffset}${issueSeverity ? `&severity=${encoded(issueSeverity)}` : ''}`, session, { signal });
      if (live.current && !signal?.aborted && epoch === issueEpoch.current && commandTarget === targetRef.current) setIssues(result);
    } catch (failure) { if (live.current && !signal?.aborted && epoch === issueEpoch.current) { if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { denied.current = true; clearPrivate(); } else setIssues(null); setError(errorMessage(failure)); } }
    finally { if (live.current && !signal?.aborted && epoch === issueEpoch.current) setIssuesLoading(false); }
  }, [detail?.latest_validation?.id, endpoint, issueOffset, issueSeverity, projectId, session, target]);
  useEffect(() => { if (step !== 3) return; const controller = new AbortController(); setIssues(null); void loadIssues(controller.signal); return () => controller.abort(); }, [loadIssues, step]);

  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    const scope = scopeRef.current;
    try { await action(); }
    catch (failure) { if (live.current && scopeRef.current === scope && !(failure instanceof DOMException && failure.name === 'AbortError')) { if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { denied.current = true; clearPrivate(); } setError(errorMessage(failure)); } }
    finally { lock.current = false; if (live.current && scopeRef.current === scope) setBusy(false); }
  }
  function current(commandTarget: string) { return live.current && !denied.current && targetRef.current === commandTarget; }
  function commandBody(action: string) { if (!detail) throw new Error('请先读取迁移项目。'); if (!reason.trim()) throw new Error('请填写本次操作的核对依据。'); return { expected_version: detail.project.version, idempotency_key: stableMigrationKey(keys.current, `${projectId}:${action}`), reason: reason.trim() }; }
  async function create() {
    if (!list?.permissions.manage) return;
    await run(async () => {
      const cutover = `${list.target.period}-01`; const invalid = migrationDateError(historyStart, historyEnd, cutover); if (invalid) throw new Error(invalid);
      if (!productVersion.trim() || !externalBook.trim() || !sourceKey.trim() || !createReason.trim()) throw new Error('请填完整产品版本、外部账套、固定来源标识和接管依据。');
      const result = await api<{ project?: MigrationProject; id?: string }>(base, session, { method: 'POST', body: { target_book_id: bookId, source_product: product, product_version: productVersion.trim(), external_book_id: externalBook.trim(), source_system_key: sourceKey.trim(), history_start: monthBoundary(historyStart), history_end: monthBoundary(historyEnd, true), cutover_date: cutover, reason: createReason.trim(), idempotency_key: stableMigrationKey(keys.current, 'create') } });
      if (!live.current) return; const id = result.project?.id ?? result.id; if (!id) throw new Error('项目已提交，请读取列表确认结果。');
      keys.current = {}; setShowCreate(false); setProjectId(id); await loadList(); onChanged?.();
    });
  }
  async function chooseFiles(files: FileList | null) {
    if (!files?.length) return;
    setFileError(''); setColumnsConfirmed(false); setHeaders([]); setColumnMapping({ columns: {}, date_format: 'ymd', allow_grouping: false });
    const selected = Array.from(files);
    if (selected.length > 20) { setFileError('每次最多选择 20 份文件，请分批上传。'); return; }
    if (replaceFile && selected.length !== 1) { setFileError('修订原件须一次选择一份对应文件，原件与修订版都会保留。'); return; }
    const invalidFile = selected.find(file => uploadSizeError(file.size));
    if (invalidFile) { setFileError(`${invalidFile.name}：${uploadSizeError(invalidFile.size)}`); return; }
    if (!['package', 'attachment'].includes(category) && selected.length > 1) { setFileError('科目、凭证等表格需逐份确认列对应；完整 JSON 包和附件可批量上传。'); return; }
    setPendingFiles(selected);
    setStoredMapping(null);
    if (category !== 'package' && category !== 'attachment' && /\.csv$/i.test(selected[0].name)) {
      try {
        const data = await selected[0].slice(0, 65536).arrayBuffer(); let text: string;
        try { text = new TextDecoder('utf-8', { fatal: true }).decode(data, { stream: true }); } catch { text = new TextDecoder('gb18030', { fatal: true }).decode(data, { stream: true }); }
        const next = parseCsvHeaders(text); if (next.length > 64) throw new Error('原表不能超过 64 列。');
        setHeaders(next); setColumnMapping(suggestMigrationColumns(next, category));
      } catch (failure) { setFileError(errorMessage(failure)); }
    }
  }
  async function upload(inspectOnly = false) {
    if (!canManage || !pendingFiles.length) return;
    await run(async () => {
      if (!inspectOnly && !['package', 'attachment'].includes(category)) {
        const invalid = migrationColumnError(category as MigrationRecordKind, columnMapping, detail!.project.history_start.slice(0, 7) !== detail!.project.history_end.slice(0, 7)); if (invalid) throw new Error(invalid);
        if (!columnsConfirmed) throw new Error('请确认原表列对应和日期、金额格式。');
      }
      const commandTarget = target; let version = detail!.project.version;
      for (const file of pendingFiles) {
        const invalidSize = uploadSizeError(file.size);
        if (invalidSize) throw new Error(`${file.name}：${invalidSize}`);
        const body = new FormData(); body.append('file', file); body.append('category', category); body.append('expected_version', String(version)); body.append('reason', reason.trim());
        body.append('idempotency_key', stableMigrationKey(keys.current, `${projectId}:file:${category}:${file.name}:${file.size}:${file.lastModified}`));
        body.append('mapping_json', JSON.stringify(['package', 'attachment'].includes(category) ? {} : inspectOnly ? {} : { ...columnMapping, confirmed: true }));
        if (replaceFile) body.append('replaces_file_id', replaceFile);
        const result = await api<{ file: MigrationFile; inspection: { headers?: string[]; suggested_columns?: Record<string, number> } }>(`${endpoint}/files`, session, { method: 'POST', body });
        if (!current(commandTarget)) return;
        const latest = normalizedMigrationDetail(await api<MigrationDetail>(endpoint, session)); if (!current(commandTarget)) return;
        setDetail(latest); version = latest.project.version; setPendingFiles(previous => previous.filter(item => item !== file));
        if (inspectOnly && !['package', 'attachment'].includes(category)) { const nextHeaders = result.inspection.headers ?? []; setHeaders(nextHeaders); setColumnMapping(suggestMigrationColumns(nextHeaders, category as MigrationRecordKind)); setStoredMapping({ file_id: result.file.id, filename: file.name, kind: category as MigrationRecordKind }); }
      }
      if (current(commandTarget)) { if (!inspectOnly) { setHeaders([]); setStoredMapping(null); } setReplaceFile(''); setColumnsConfirmed(false); setConfirmed(false); keys.current = {}; setNotice(inspectOnly ? '原件已保留，请确认表头对应后保存资料版本，尚未开始解析。' : '原始资料已保留。系统解析后再核对，不会生成当前凭证或重复导出。'); if (input.current) input.current.value = ''; onChanged?.(); }
    });
  }
  async function openFileMapping(file: MigrationFile) {
    if (!canManage || ['package', 'attachment'].includes(file.category)) return;
    await run(async () => {
      const commandTarget = target;
      const result = await api<{ headers: string[]; suggested_columns?: Record<string, number> }>(`${endpoint}/files/${encoded(file.id)}/inspection`, session);
      if (current(commandTarget)) { setCategory(file.category); setStep(0); setPendingFiles([]); setHeaders(result.headers); setColumnMapping(Object.keys(file.mapping?.columns ?? {}).length ? file.mapping! : suggestMigrationColumns(result.headers, file.category as MigrationRecordKind)); setStoredMapping({ file_id: file.id, filename: file.filename, kind: file.category as MigrationRecordKind }); setColumnsConfirmed(false); }
    });
  }
  async function saveFileMapping() {
    if (!storedMapping || !canManage || !columnsConfirmed) return;
    await run(async () => {
      const invalid = migrationColumnError(storedMapping.kind, columnMapping, detail!.project.history_start.slice(0, 7) !== detail!.project.history_end.slice(0, 7)); if (invalid) throw new Error(invalid);
      const commandTarget = target;
      await api(`${endpoint}/files/${encoded(storedMapping.file_id)}/remap`, session, { method: 'POST', body: { ...commandBody(`file-mapping:${storedMapping.file_id}`), mapping: { ...columnMapping, confirmed: true } } });
      if (current(commandTarget)) { keys.current = {}; setStoredMapping(null); setHeaders([]); setColumnsConfirmed(false); await loadDetail(); setNotice('列对应新版本已明确确认，原件和修订关系保留；可以发起后台解析。'); onChanged?.(); }
    });
  }
  async function enqueue(kind: 'parse' | 'validate') {
    if (!canManage || dirty) return;
    await run(async () => { const commandTarget = target; await api(`${endpoint}/${kind}`, session, { method: 'POST', body: commandBody(kind) }); if (current(commandTarget)) { keys.current = {}; await loadDetail(); setStep(kind === 'parse' ? 1 : 3); setNotice('任务已进入持久队列，离开页面后仍会继续；稍后返回可查看进度。'); onChanged?.(); } });
  }
  async function saveMapping() {
    if (!canManage || !dirty) return;
    await run(async () => {
      const accounts = migrationPostingMappings(Object.values(accountEdits), records?.records ?? []), auxiliaries = Object.values(auxEdits);
      if (accounts.some(row => !row.target_code.trim())) throw new Error('每项科目映射都须填写目标科目编码。');
      if (auxiliaries.some(row => row.action === 'use_existing' && !row.target_id?.trim())) throw new Error('使用已有辅助档案时，请填写该档案的稳定 ID。');
      const commandTarget = target;
      const splitCodes = (value: string) => value.split(/[，,\s]+/).filter(Boolean);
      await api(`${endpoint}/mapping`, session, { method: 'POST', body: { ...commandBody('mapping'), accounts, auxiliaries, ...(settlementEdits ? { settlement_accounts: { receivable: splitCodes(settlementEdits.receivable), payable: splitCodes(settlementEdits.payable) } } : {}) } });
      if (current(commandTarget)) { keys.current = {}; setAccountEdits({}); setAuxEdits({}); setSettlementEdits(null); setConfirmed(false); await loadDetail(); await loadRecords(); setNotice('映射新版本已保存。旧验收结果失效，请重新运行完整校验。'); onChanged?.(); }
    });
  }
  async function finalize(action: 'accept' | 'activate') {
    if (!detail?.latest_validation || !confirmed || dirty) return;
    await run(async () => {
      const commandTarget = target; const validation = detail.latest_validation!;
      await api(`${endpoint}/${action}`, session, { method: 'POST', body: { ...commandBody(action), validation_id: validation.id, basis_token: validation.basis_token, confirmed: true } });
      if (current(commandTarget)) { keys.current = {}; setConfirmed(false); await loadDetail(); setNotice(action === 'accept' ? '独立验收已记录。由长期账套管理人确认接管。' : '接管快照已冻结，历史凭证保留在归档查询中；新月份继续经过会计审核后导出。'); onChanged?.(); }
    });
  }
  async function jobAction(job: MigrationJob, action: 'retry' | 'cancel') {
    await run(async () => { const commandTarget = target; await api(`${endpoint}/jobs/${encoded(job.id)}/${action}`, session, { method: 'POST', body: commandBody(`${job.id}:${action}`) }); if (current(commandTarget)) { keys.current = {}; await loadDetail(); setNotice(action === 'retry' ? '失败任务已重新排队，不会重复写入归档。' : '取消请求已记录，请读取任务最终状态。'); } });
  }
  async function getOriginal(fileId: string) {
    const file = detail?.files.find(item => item.id === fileId); if (!file) { setError('原文件不存在或不属于当前迁移项目，请读取最新资料。'); return; }
    await run(async () => { const commandTarget = target; const value = await apiBlob(`${endpoint}/files/${encoded(fileId)}`, session); if (current(commandTarget)) download(value, file.filename); });
  }
  async function loadAccess() {
    if (!detail?.permissions.manage) return;
    await run(async () => { const commandTarget = target; const result = await api<NonNullable<typeof accessData>>(`${endpoint}/access`, session); if (current(commandTarget)) setAccessData(result); });
  }
  async function revokeAccess(userId: string) {
    if (!grantReason.trim() || !detail?.permissions.manage) return;
    await run(async () => { const commandTarget = target; await api(`${endpoint}/access/${encoded(userId)}`, session, { method: 'DELETE', body: { reason: grantReason.trim() } }); if (!current(commandTarget)) return; const latest = await api<NonNullable<typeof accessData>>(`${endpoint}/access`, session); if (current(commandTarget)) { setAccessData(latest); await loadDetail(); setNotice('该人员的明确历史授权已撤销，当前账套权限独立保留。'); } });
  }
  function selectProject(id: string) { if (busy || dirty) return; setProjectId(id); setShowCreate(false); setNotice(''); setError(''); }
  const validation = detail?.latest_validation;
  const hasUnconfirmedFiles = detail?.files.some(migrationNeedsColumnConfirmation) ?? false;
  const acceptanceCurrent = !!detail && migrationAcceptanceCurrent(detail);
  const valid = !!validation && validation.current !== false && validation.coverage?.complete !== false && validation.blocking_count === 0 && !['stale', 'failed', 'invalid', 'running', 'queued'].includes(validation.state) && validation.valid !== false;
  const mayAccept = !!detail?.permissions.review && valid && detail.can_accept !== false && !acceptanceCurrent && !frozen && detail.target.period_status !== 'closed';
  const mayActivate = !!detail?.permissions.activate && valid && acceptanceCurrent && detail.can_activate !== false && !frozen && detail.target.period_status !== 'closed';

  return <main className="legacy-migration-workspace">
    <header className="legacy-migration-heading"><div><p className="eyebrow">旧账迁移 · 企业接管</p><h1>把旧账接清楚，再开始新月份。</h1><p>原始资料、历史凭证与接管依据一起留存，集中处理需要会计判断的差异。</p></div><button className="button secondary" disabled={busy || dirty} onClick={onBack}><ArrowLeft size={16} />返回账套</button></header>
    <section className="legacy-migration-boundary" aria-label="迁移范围"><ShieldCheck size={22} /><div><strong>{list?.target.company_name ?? detail?.target?.company_name ?? '正在核验企业授权'} · {list?.target.period ?? detail?.target?.period ?? '—'} 接管</strong><p>客户原软件继续承担正式账本。历史凭证独立归档，接管资料不会再次进入当前凭证导出。</p></div><span className="legacy-migration-mode">文件接入</span></section>
    {error && <p role="alert" className="inline-error legacy-migration-feedback"><TriangleAlert size={17} />{error}</p>}{notice && <p role="status" className="form-success legacy-migration-feedback"><CheckCircle2 size={17} />{notice}</p>}
    {error && !list && !loading && <button className="button secondary" disabled={busy} onClick={() => void run(async () => { await loadList(); if (projectId) await loadDetail(); })}><RefreshCw size={15} />重新核验当前授权</button>}
    {loading && !list ? <div className="loading-block"><LoaderCircle className="spinner" size={18} />正在读取迁移权限与历史项目…</div> : list && <>
      <div className="legacy-migration-projectbar"><label>当前迁移项目<select aria-label="选择旧账迁移项目" value={projectId} disabled={busy || dirty} onChange={event => selectProject(event.target.value)}><option value="">请选择迁移项目</option>{list.projects.map(project => <option key={project.id} value={project.id}>{MIGRATION_PRODUCTS[project.source_product]} · {project.external_book_id} · {project.history_start}–{project.history_end} · {migrationState(project.state)}</option>)}{detail && !list.projects.some(project => project.id === detail.project.id) && <option value={detail.project.id}>{MIGRATION_PRODUCTS[detail.project.source_product]} · {detail.project.external_book_id} · {migrationState(detail.project.state)}</option>}</select></label><button className="button secondary" disabled={disabled || dirty} onClick={() => void run(async () => { await loadList(); if (projectId) await loadDetail(); })}><RefreshCw size={15} />读取最新状态</button>{list.permissions.manage && <button className="button primary" disabled={disabled || dirty || list.permissions.can_create === false} onClick={() => { setShowCreate(value => !value); setProjectId(''); setError(''); }}><Plus size={16} />新建迁移</button>}</div>
      {list.pagination?.total > PAGE_SIZE && <Pagination total={list.pagination.total} offset={listOffset} hasMore={list.pagination.has_more} disabled={disabled || dirty} onPage={setListOffset} />}
      {(!projectId || showCreate) && <section className="legacy-migration-card legacy-migration-intro"><div className="legacy-migration-card-title"><FolderClock size={25} /><div><h2>{showCreate ? '确定接管范围' : '开始一项可复核的旧账迁移'}</h2><p>先选择来源和历史月份，再保留整包资料。试点需核验客户实际导出格式与软件版本。</p></div></div>{showCreate ? <><div className="legacy-migration-formgrid"><label>来源软件<select aria-label="旧账来源软件" value={product} onChange={event => { setProduct(event.target.value as MigrationProduct); keys.current = {}; }} disabled={busy}>{Object.entries(MIGRATION_PRODUCTS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>具体产品 / 版本<input aria-label="旧账产品版本" value={productVersion} disabled={busy} maxLength={100} placeholder="例如 T+ 17.0，需按实际核验" onChange={event => { setProductVersion(event.target.value); keys.current = {}; }} /></label><label>外部账套标识<input aria-label="旧账外部账套标识" value={externalBook} disabled={busy} maxLength={100} placeholder="原软件账套 ID，不能只填公司简称" onChange={event => { setExternalBook(event.target.value); keys.current = {}; }} /></label><label>固定来源系统标识<input aria-label="旧账固定来源系统标识" value={sourceKey} disabled={busy} maxLength={100} placeholder="例如 tplus-client-001" onChange={event => { setSourceKey(event.target.value); keys.current = {}; }} /></label><label>历史开始月份<input aria-label="旧账历史开始月份" type="month" value={historyStart} max={historyEnd || list.target.period} disabled={busy} onChange={event => { setHistoryStart(event.target.value); keys.current = {}; }} /></label><label>历史结束月份<input aria-label="旧账历史结束月份" type="month" value={historyEnd} min={historyStart} max={list.target.period} disabled={busy} onChange={event => { setHistoryEnd(event.target.value); keys.current = {}; }} /></label><label>接管日期<input aria-label="旧账接管日期" readOnly value={`${list.target.period}-01`} /><small>目标期间首日，接管期须为空且开放。</small></label><label>建立项目依据<input aria-label="建立旧账迁移项目依据" value={createReason} disabled={busy} maxLength={1000} placeholder="资料范围、来源与接管目的" onChange={event => { setCreateReason(event.target.value); keys.current = {}; }} /></label></div><div className="legacy-migration-actions"><button className="button primary" disabled={busy || !historyStart || !historyEnd || !externalBook.trim() || !productVersion.trim() || !sourceKey.trim() || !createReason.trim()} onClick={() => void create()}>建立迁移项目<ArrowRight size={16} /></button><button className="button secondary" disabled={busy} onClick={() => setShowCreate(false)}>暂不创建</button></div></> : <><ol className="legacy-migration-introsteps">{MIGRATION_STEPS.map((label, index) => <li key={label}><span>{String(index + 1).padStart(2, '0')}</span><strong>{label}</strong></li>)}</ol><p className="legacy-migration-help">科目、辅助档案、凭证、余额与有依据的未结往来分别核对。未经验证的外币、数量、存货等核算不能省略字段后接管。</p>{!list.permissions.manage && <p className="legacy-migration-access"><LockKeyhole size={16} />当前可查看已授权历史项目。建立项目需要长期账套管理授权及当前期间复核权。</p>}</>}</section>}
      {projectId && !detail && <div className="loading-block"><LoaderCircle className="spinner" size={18} />正在读取所选迁移项目…</div>}
      {detail && <>
        <section className="legacy-migration-projectmeta"><div><span className="legacy-migration-kicker">{MIGRATION_PRODUCTS[detail.project.source_product]} · {detail.project.product_version}</span><h2>{detail.project.external_book_id}</h2><p>{detail.project.history_start} 至 {detail.project.history_end} · {detail.project.cutover_date} 接管 · <span>{migrationState(detail.project.state)}</span></p></div><details><summary>项目与格式信息</summary><p>固定来源：{detail.project.source_system_key}</p><p>版本 {detail.project.version} · 格式适配须按客户实际文件核验，目前不代表已连接原软件。</p><p>权限：{[['read', '查看'], ['manage', '维护'], ['review', '独立验收'], ['activate', '确认接管']].filter(([key]) => detail.permissions[key as keyof typeof detail.permissions]).map(([, label]) => label).join('、') || '无'}</p></details></section>
        <ol className="legacy-migration-stepper" aria-label="旧账迁移步骤">{MIGRATION_STEPS.map((label, index) => <li key={label} aria-current={step === index ? 'step' : undefined}><button type="button" disabled={busy || dirty} onClick={() => setStep(index)} className={step === index ? 'current' : index < migrationStep(detail) ? 'complete' : ''}><span>{index < migrationStep(detail) ? <Check size={16} /> : index + 1}</span>{label}</button></li>)}</ol>
        {frozen && <div className="legacy-migration-frozen"><LockKeyhole size={18} /><div><strong>接管快照已冻结</strong><p>历史资料只读保留；新月份业务继续按审批、生成草稿、会计复核和明确导出流程处理。</p></div></div>}
        {!frozen && <label className="legacy-migration-reason">本次操作的核对依据<input aria-label="旧账迁移操作依据" value={reason} maxLength={1000} disabled={busy} placeholder="填写解析、映射、校验或接管依据" onChange={event => { setReason(event.target.value); keys.current = {}; setConfirmed(false); }} /></label>}
        {step === 0 && <section className="legacy-migration-card"><div className="legacy-migration-card-title"><Upload size={24} /><div><h2>一次准备好旧账资料</h2><p>JSON 完整资料包可批量选择；CSV / Excel 逐份确认列对应。所有原始文件保留在当前企业。每份最多 {uploadLimitLabel()}，大文件请按历史期间拆分。</p></div></div>{canManage && <><div className="legacy-migration-uploadrow"><label>资料类别<select aria-label="旧账资料类别" value={category} disabled={busy} onChange={event => { setCategory(event.target.value as MigrationCategory); setStoredMapping(null); setReplaceFile(''); setPendingFiles([]); setHeaders([]); setColumnsConfirmed(false); setFileError(''); }}>{Object.entries(MIGRATION_CATEGORIES).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><input ref={input} type="file" multiple={category === 'package' || category === 'attachment'} accept={category === 'package' ? '.json' : category === 'attachment' ? '.pdf,.png,.jpg,.jpeg,.webp' : '.csv,.xlsx'} hidden aria-label="选择旧账资料文件" disabled={busy} onChange={event => void chooseFiles(event.target.files)} /><button className="legacy-migration-upload" type="button" disabled={busy} onClick={() => input.current?.click()}><FileArchive size={24} /><span><strong>{pendingFiles.length ? `已选择 ${pendingFiles.length} 份文件` : '选择旧账资料'}</strong><small>{category === 'package' ? '通用 JSON 包，不能直接假定为原软件备份文件' : category === 'attachment' ? '凭证附件原件，随项目归档保留' : '支持 UTF-8 / GBK CSV、无公式单工作表 Excel'}</small></span></button></div>{pendingFiles.length > 0 && <ul className="legacy-migration-pending">{pendingFiles.map(file => <li key={`${file.name}:${file.size}`}><FileSpreadsheet size={16} /><span>{file.name}</span><small>{Math.ceil(file.size / 1024)} KB</small></li>)}</ul>}{fileError && <p className="inline-error" role="alert">{fileError}</p>}
          {(pendingFiles.length > 0 || storedMapping) && !['package', 'attachment'].includes(category) && <ColumnMapper multiplePeriods={detail.project.history_start.slice(0, 7) !== detail.project.history_end.slice(0, 7)} kind={category as MigrationRecordKind} headers={headers} mapping={columnMapping} confirmed={columnsConfirmed} disabled={busy} onMapping={value => { setColumnMapping(value); setColumnsConfirmed(false); keys.current = {}; }} onConfirm={setColumnsConfirmed} />}
          {storedMapping && <div className="legacy-migration-unsaved" role="status"><span>正在确认已保留原件：{storedMapping.filename}</span><button className="button secondary small" disabled={busy} onClick={() => { setStoredMapping(null); setHeaders([]); setColumnsConfirmed(false); }}>稍后继续</button><button className="button primary small" disabled={busy || !columnsConfirmed || !reason.trim()} onClick={() => void saveFileMapping()}>保存明确列对应</button></div>}
          {pendingFiles.length > 0 && !['package', 'attachment'].includes(category) && <button className="button secondary" disabled={busy || !reason.trim()} onClick={() => void upload(true)}><FileSpreadsheet size={16} />读取表头并保留原件</button>}
          {hasUnconfirmedFiles && <p className="legacy-migration-help">仍有未确认列对应的表格，完成下方原件的“确认原表列”后才能解析。</p>}
          {detail.files.some(file => file.category === category && !file.superseded) && <label>本次资料用途<select aria-label="旧账修订原件选择" value={replaceFile} disabled={busy} onChange={event => { setReplaceFile(event.target.value); keys.current = {}; }}><option value="">补充新资料，不替代原件</option>{detail.files.filter(file => file.category === category && !file.superseded).map(file => <option key={file.id} value={file.id}>修订并替代：{file.filename}</option>)}</select><small>修订时保留原件、修订版和替代关系，原件不再参与当前校验。</small></label>}
          <div className="legacy-migration-actions"><button className="button primary" disabled={busy || !pendingFiles.length || !!fileError || !reason.trim() || (!['package', 'attachment'].includes(category) && !columnsConfirmed)} onClick={() => void upload(false)}><Upload size={16} />保留所选资料</button><button className="button secondary" disabled={busy} onClick={() => void run(async () => { const commandTarget = target; const value = await apiBlob(`${base}/template?external_book_id=${encoded(detail.project.external_book_id)}&history_start=${encoded(detail.project.history_start)}&history_end=${encoded(detail.project.history_end)}&cutover_date=${encoded(detail.project.cutover_date)}`, session); if (current(commandTarget)) download(value, 'SYNTHETIC-旧账通用资料示例.json'); })}><Download size={16} />下载合成资料示例</button></div></>}
          <FileList files={detail.files} busy={busy} onOriginal={getOriginal} onMapping={canManage ? openFileMapping : undefined} />{!frozen && <div className="legacy-migration-footer"><p>{detail.files.length} 份原始资料已保留，继续解析完整历史范围。</p><button className="button primary" disabled={busy || !canManage || !detail.files.length || !reason.trim() || activeJobs || hasUnconfirmedFiles || !!storedMapping} onClick={() => void enqueue('parse')}>开始后台解析<ArrowRight size={16} /></button></div>}
        </section>}
        {step === 1 && <section className="legacy-migration-card"><div className="legacy-migration-card-title"><FolderClock size={25} /><div><h2>后台处理，随时回来查看</h2><p>任务持久保存、企业间公平排队。失败只重试该任务，原件和已解析依据继续保留。</p></div></div><Jobs jobs={detail.jobs} busy={busy} manage={canManage} hasReason={!!reason.trim()} onAction={jobAction} />{!detail.jobs.length && <div className="legacy-migration-empty"><History size={26} /><strong>尚未开始解析</strong><p>返回上传资料页，保留文件后开始后台解析。</p></div>}<div className="legacy-migration-footer"><p>解析完成后，确认企业科目与辅助项对应。解析不会调整旧账金额。</p><button className="button primary" disabled={busy || activeJobs} onClick={() => setStep(2)}>确认科目与辅助映射<ArrowRight size={16} /></button></div></section>}
        {step === 2 && <section className="legacy-migration-card"><div className="legacy-migration-card-title"><FileCheck2 size={25} /><div><h2>{frozen ? '查询历史归档' : '保留原科目，确认企业对应'}</h2><p>只展示当前页资料；新增项目逐项确认。历史凭证、余额与辅助原名完整保留。</p></div></div><div className="legacy-migration-filters"><label>历史资料<select aria-label="旧账历史资料类型" disabled={busy || dirty} value={recordKind} onChange={event => { setRecordKind(event.target.value as MigrationRecordKind); setRecordOffset(0); setFocus(null); }}>{Object.entries(MIGRATION_CATEGORIES).filter(([key]) => !['package', 'attachment'].includes(key)).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label><label>会计期间<input type="month" aria-label="旧账历史资料期间" value={recordPeriod} disabled={busy || dirty} min={detail.project.history_start.slice(0, 7)} max={detail.project.history_end.slice(0, 7)} onChange={event => { setRecordPeriod(event.target.value); setRecordOffset(0); }} /></label><button className="button secondary" disabled={busy || dirty || recordsLoading} onClick={() => void loadRecords()}><RefreshCw size={15} />读取当前页</button></div>
          {canManage && <details className="legacy-migration-settlement-settings" open={recordKind === 'open_items' || undefined}><summary>往来余额核对范围</summary><p>有历史未结往来时，明确选择应收、应付来源科目。系统按科目与往来方辅助余额复算；只有汇总余额时不能自动还原明细。</p><div className="legacy-migration-formgrid"><label>应收来源科目编码<input aria-label="旧账应收来源科目编码" value={settlementEdits?.receivable ?? (detail.mapping?.settlement_accounts?.receivable ?? []).join(', ')} placeholder="例如 1122，多个编码以逗号分隔" disabled={busy} onChange={event => { setSettlementEdits(previous => ({ receivable: event.target.value, payable: previous?.payable ?? (detail.mapping?.settlement_accounts?.payable ?? []).join(', ') })); keys.current = {}; }} /></label><label>应付来源科目编码<input aria-label="旧账应付来源科目编码" value={settlementEdits?.payable ?? (detail.mapping?.settlement_accounts?.payable ?? []).join(', ')} placeholder="例如 2202，须按实际账务选择" disabled={busy} onChange={event => { setSettlementEdits(previous => ({ payable: event.target.value, receivable: previous?.receivable ?? (detail.mapping?.settlement_accounts?.receivable ?? []).join(', ') })); keys.current = {}; }} /></label></div></details>}
          {recordsLoading ? <div className="loading-block"><LoaderCircle className="spinner" size={18} />读取历史资料…</div> : records?.records.length ? <div className="legacy-migration-records">{records.records.map((record, index) => {
            const code = recordCode(record), data = recordData(record), auxiliaryKind = String(data.kind ?? record.kind ?? 'counterparty'), auxKey = `${auxiliaryKind}:${code}`;
            const account = accountEdits[code] ?? (records.mapping ?? detail.mapping)?.accounts?.find(row => row.source_code === code) ?? record.mapping as MigrationAccountMapping | undefined;
            const auxiliary = auxEdits[auxKey] ?? (records.mapping ?? detail.mapping)?.auxiliaries?.find(row => row.source_code === code && row.kind === auxiliaryKind) ?? record.mapping as MigrationAuxMapping | undefined;
            return <article key={record.id ?? `${code}:${index}`} className="legacy-migration-record"><div className="legacy-migration-record-title"><strong>{recordName(record)}</strong><span>{code || String(record.external_id ?? '原记录')}</span><button type="button" className="text-button" disabled={busy} onClick={() => setFocus({ title: recordName(record), record })}>原始字段与依据</button></div>
              {recordKind === 'accounts' && migrationArchiveOnlyAccount(record) && <p className="legacy-migration-record-caption"><span className="badge pending">上级科目，仅归档</span> 保留原科目树，无需目标映射，也不创建可记账上级科目。</p>}
              {recordKind === 'accounts' && canManage && !migrationArchiveOnlyAccount(record) && <div className="legacy-migration-maprow"><label>对应方式<select aria-label={`科目 ${code} 对应方式`} value={account?.action ?? 'use_existing'} disabled={busy} onChange={event => { setAccountEdits(previous => ({ ...previous, [code]: { source_code: code, target_code: account?.target_code ?? code, action: event.target.value as MigrationAccountMapping['action'] } })); keys.current = {}; }}><option value="use_existing">使用企业已有科目</option><option value="create">保留编码并建立科目</option></select></label><label>目标科目编码<input aria-label={`科目 ${code} 目标编码`} value={account?.target_code ?? ''} placeholder={code} disabled={busy} onChange={event => { setAccountEdits(previous => ({ ...previous, [code]: { source_code: code, target_code: event.target.value, action: account?.action ?? 'use_existing' } })); keys.current = {}; }} /></label><button className="button secondary small" disabled={busy} onClick={() => { setAccountEdits(previous => ({ ...previous, [code]: { source_code: code, target_code: code, action: 'create' } })); keys.current = {}; }}>沿用原编码</button></div>}
              {recordKind === 'auxiliaries' && canManage && <div className="legacy-migration-maprow"><label>对应方式<select aria-label={`辅助 ${auxKey} 对应方式`} value={auxiliary?.action ?? 'use_existing'} disabled={busy} onChange={event => { setAuxEdits(previous => ({ ...previous, [auxKey]: { source_code: code, kind: auxiliaryKind, target_id: auxiliary?.target_id, action: event.target.value as MigrationAuxMapping['action'] } })); keys.current = {}; }}><option value="use_existing">使用企业已有档案</option><option value="create">按原编码建立档案</option></select></label><label>目标档案稳定 ID<input aria-label={`辅助 ${auxKey} 目标ID`} value={auxiliary?.target_id ?? ''} disabled={busy || auxiliary?.action === 'create'} placeholder="已有企业主数据 ID" onChange={event => { setAuxEdits(previous => ({ ...previous, [auxKey]: { source_code: code, kind: auxiliaryKind, target_id: event.target.value, action: 'use_existing' } })); keys.current = {}; }} /></label><button className="button secondary small" disabled={busy} onClick={() => { setAuxEdits(previous => ({ ...previous, [auxKey]: { source_code: code, kind: auxiliaryKind, action: 'create' } })); keys.current = {}; }}>建立原档案</button></div>}
              {recordKind !== 'accounts' && recordKind !== 'auxiliaries' && <p className="legacy-migration-record-caption">{String(data.period ?? record.period ?? '')}{data.date ? ` · ${String(data.date)}` : ''}{data.debit !== undefined ? ` · 借 ${migrationMoney(data.debit)}` : ''}{data.credit !== undefined ? ` · 贷 ${migrationMoney(data.credit)}` : ''}{data.remaining_amount !== undefined ? ` · 未结 ${migrationMoney(data.remaining_amount)}` : ''}</p>}
              {!canManage && (recordKind === 'accounts' && !migrationArchiveOnlyAccount(record) || recordKind === 'auxiliaries') && <p className="legacy-migration-record-caption">{account?.target_code || auxiliary?.target_id ? `已映射：${account?.target_code ?? auxiliary?.target_id}` : '原档案保留，映射由明确授权的迁移维护人员确认'}</p>}
            </article>;
          })}</div> : <div className="legacy-migration-empty"><FileSpreadsheet size={26} /><strong>当前条件下没有历史资料</strong><p>确认解析已完成，或切换资料类型和历史期间。</p></div>}
          {records?.pagination && <Pagination total={records.pagination.total} offset={recordOffset} hasMore={records.pagination.has_more} disabled={busy || dirty || recordsLoading} onPage={setRecordOffset} />}
          {dirty && <div className="legacy-migration-unsaved" role="status"><span>有 {Object.keys(accountEdits).length + Object.keys(auxEdits).length + (settlementEdits ? 1 : 0)} 项未保存配置，保存后才能换页或校验。</span><button className="button secondary small" disabled={busy} onClick={() => { setAccountEdits({}); setAuxEdits({}); setSettlementEdits(null); }}>放弃本页改动</button><button className="button primary small" disabled={busy || !reason.trim()} onClick={() => void saveMapping()}>保存映射新版本</button></div>}
          {!frozen && <div className="legacy-migration-footer"><p>映射变更后重新复算完整资料，金额由确定程序校验。</p><button className="button primary" disabled={busy || dirty || !canManage || !reason.trim() || activeJobs} onClick={() => void enqueue('validate')}>运行完整校验<ArrowRight size={16} /></button></div>}
        </section>}
        {step === 3 && <section className="legacy-migration-card"><div className="legacy-migration-card-title"><ShieldCheck size={25} /><div><h2>只处理需要判断的差异</h2><p>原文件位置、影响和下一步操作一起展示。金额差异不会由 AI 自动填平。</p></div></div>{validation ? <><div className="legacy-migration-validation" aria-label="迁移校验概览"><div className={validation.blocking_count ? 'blocking' : 'passed'}><span>阻断问题</span><strong>{validation.blocking_count}</strong><small>需全部解决后才能验收</small></div><div><span>需复核提醒</span><strong>{validation.warning_count}</strong><small>会计确认原件与业务范围</small></div><div><span>当前校验</span><strong className="legacy-migration-result-label">{migrationState(validation.state)}</strong><small>映射 v{validation.mapping_version ?? '—'}</small></div></div><ValidationSummary validation={validation} /><div className="legacy-migration-filters"><label>差异级别<select aria-label="旧账差异级别" disabled={busy} value={issueSeverity} onChange={event => { setIssueSeverity(event.target.value); setIssueOffset(0); }}><option value="blocking">阻断接管</option><option value="warning">需要复核</option><option value="">全部差异</option></select></label><button className="button secondary" disabled={busy || issuesLoading} onClick={() => void loadIssues()}><RefreshCw size={15} />读取当前差异</button></div>{issuesLoading ? <div className="loading-block"><LoaderCircle className="spinner" size={18} />读取差异…</div> : issues?.issues.length ? <div className="legacy-migration-issues">{issues.issues.map((issue, index) => <article className={`legacy-migration-issue ${issue.severity}`} key={issue.id ?? `${issue.code}:${index}`}><div><span className={`badge ${issue.severity === 'blocking' ? 'exception' : 'pending'}`}>{issue.severity === 'blocking' ? '阻断' : issue.severity === 'warning' ? '复核' : '说明'}</span><strong>{issue.message}</strong></div><p>{issue.next_action || '核对原表与映射，修正资料后重新解析和校验。'}</p><div><small>{issue.row_number ? `原表第 ${issue.row_number} 行` : '完整历史范围'}{issue.external_id ? ` · ${issue.external_id}` : ''}</small><button className="text-button" disabled={busy} onClick={() => setFocus({ title: '差异与原始依据', issue })}>查看依据与处理建议</button></div></article>)}</div> : <div className="legacy-migration-empty"><CheckCircle2 size={28} /><strong>{issueSeverity === 'blocking' ? '当前页没有阻断差异' : '当前条件下没有差异'}</strong><p>以完整校验统计为准；切换级别查看复核提醒。</p></div>}{issues?.pagination && <Pagination total={issues.pagination.total} offset={issueOffset} hasMore={issues.pagination.has_more} disabled={busy || issuesLoading} onPage={setIssueOffset} />}</> : <div className="legacy-migration-empty"><FileCheck2 size={26} /><strong>等待完整校验报告</strong><p>确认科目映射后发起后台校验，报告随资料版本冻结。</p></div>}<div className="legacy-migration-footer"><p>解析、文件、映射变化都会使旧验收失效。</p><div className="legacy-migration-actions">{!frozen && <button className="button secondary" disabled={busy || dirty || activeJobs || !canManage || !reason.trim()} onClick={() => void enqueue('validate')}>重新完整校验</button>}<button className="button primary" disabled={busy || !valid} onClick={() => setStep(4)}>核对接管快照<ArrowRight size={16} /></button></div></div></section>}
        {step === 4 && <section className="legacy-migration-card"><div className="legacy-migration-card-title"><LockKeyhole size={25} /><div><h2>{frozen ? '接管依据已经冻结' : '由会计验收，再确认接管'}</h2><p>独立验收和接管分别记录，资料及映射变化必须重新校验。</p></div></div><div className="legacy-migration-acceptance"><div><span>01</span><strong>完整校验</strong><p>{valid ? '当前校验无阻断问题' : '请先取得当前版本的有效校验报告'}</p>{valid ? <CheckCircle2 size={21} /> : <FolderClock size={21} />}</div><div><span>02</span><strong>独立会计验收</strong><p>{acceptanceCurrent ? `已记录 · ${detail.acceptance?.created_at ?? ''}` : detail.acceptance ? '资料已变化，旧验收保留；当前版本需要重新验收' : '由另一名获授权复核人员确认'}</p>{acceptanceCurrent ? <CheckCircle2 size={21} /> : <FolderClock size={21} />}</div><div><span>03</span><strong>确认接管</strong><p>{frozen ? `${detail.project.cutover_date} 接管快照已冻结` : '长期账套管理人启用接管快照'}</p>{frozen ? <CheckCircle2 size={21} /> : <FolderClock size={21} />}</div></div><div className="legacy-migration-acceptance-note"><h3>本次确认的范围</h3><ul><li>{detail.project.history_start} 至 {detail.project.history_end} 的历史资料完整留存。</li><li>凭证、余额、未结往来保持独立口径，接管余额不向原软件重复导入。</li><li>旧欠款后续付款按核销处理，无法还原的明细不会自动生成。</li><li>历史记录不会直接变成已发布记账规则或训练答案。</li></ul></div>{!frozen && <><label className="legacy-migration-check"><input aria-label="确认旧账验收范围与差异" type="checkbox" checked={confirmed} disabled={busy || !valid} onChange={event => setConfirmed(event.target.checked)} />我已核对当前版本的原件、历史范围和全部差异，确认没有未解释的金额或资料缺口。</label><div className="legacy-migration-actions">{mayAccept && <button className="button primary" disabled={busy || !confirmed || !reason.trim()} onClick={() => void finalize('accept')}><ShieldCheck size={17} />记录独立会计验收</button>}{mayActivate && <button className="button primary" disabled={busy || !confirmed || !reason.trim()} onClick={() => void finalize('activate')}><LockKeyhole size={17} />确认接管当前期间</button>}</div>{!mayAccept && !acceptanceCurrent && <p className="legacy-migration-help">需要当前期间复核权限与明确的历史验收授权，由另一名会计验收；创建人不能验收自己的迁移项目。</p>}{acceptanceCurrent && !mayActivate && <p className="legacy-migration-help">独立验收后，由具有明确长期账套管理授权的人员确认接管。</p>}</>}
          <div className="legacy-migration-actions"><button className="button secondary" disabled={busy || !validation} onClick={() => void run(async () => { const commandTarget = target; const value = await apiBlob(`${endpoint}/report`, session); if (current(commandTarget)) download(value, `旧账迁移验收报告-${detail.project.external_book_id}.json`); })}><Download size={16} />下载当前验收报告</button><button className="button secondary" disabled={busy || dirty} onClick={() => setStep(2)}><History size={16} />查询历史归档</button></div>
          {frozen && <section className="legacy-migration-history-reuse"><button className="button secondary" disabled={busy} onClick={() => setShowSettlements(value => !value)}><FileCheck2 size={16} />{showSettlements ? '收起历史未结清偿' : '处理历史未结清偿'}</button>{showSettlements && <LegacyMigrationSettlements key={target} session={session} bookId={bookId} projectId={projectId} bankBooks={detail.eligible_bank_books ?? []} onBusyChange={setSettlementBusy} onChanged={onChanged} onOpenDraft={onOpenDraft} onLostAccess={() => { denied.current = true; clearPrivate(); setError('历史或银行复核权限已变化，资料已收起。'); }} />}</section>}
          {frozen && <section className="legacy-migration-history-reuse"><button className="button secondary" disabled={busy} onClick={() => setShowLearning(value => !value)}><History size={16} />{showLearning ? '收起历史经验复用' : '审核历史样例与企业规则'}</button>{showLearning && <LegacyMigrationLearning key={target} session={session} bookId={bookId} projectId={projectId} canCreate={detail.permissions.read} onBusyChange={setLearningBusy} onChanged={onChanged} onLostAccess={() => { denied.current = true; clearPrivate(); setError('历史授权已变化，归档资料已收起。'); }} onEvidence={record => setFocus({ title: recordName(record), record })} />}</section>}
          {detail.permissions.manage && <details className="legacy-migration-access-settings" onToggle={event => { if (event.currentTarget.open && !accessData && !busy) void loadAccess(); }}><summary>历史资料授权</summary><p>历史授权与当前账套权限同时生效。只有项目维护人可以授权，撤销当前企业或账套权限会同时失去历史访问。</p><div className="legacy-migration-formgrid"><label>当前账套成员<select aria-label="旧账历史授权人员" value={grantUser} disabled={busy || !accessData} onChange={event => setGrantUser(event.target.value)}><option value="">选择当前有效成员</option>{accessData?.choices.map(member => <option key={member.user_id} value={member.user_id}>{member.name} · {member.role === 'reviewer' ? '账套复核' : member.role === 'editor' ? '账套经办' : '账套查看'}</option>)}</select><small>独立验收还需当前复核权限；维护迁移需明确长期账套管理授权。</small></label><label>历史访问角色<select aria-label="旧账历史授权角色" value={grantRole} disabled={busy} onChange={event => setGrantRole(event.target.value as typeof grantRole)}><option value="viewer">查看历史</option><option value="manager">维护迁移资料</option><option value="reviewer">独立验收历史</option></select></label><label>授权依据<input aria-label="旧账历史授权依据" value={grantReason} disabled={busy} maxLength={1000} placeholder="明确本人员可访问的历史范围及职责" onChange={event => setGrantReason(event.target.value)} /></label></div><button className="button secondary" disabled={busy || !grantUser.trim() || !grantReason.trim()} onClick={() => void run(async () => { const commandTarget = target; const grants = await api<NonNullable<typeof accessData>>(`${endpoint}/access`, session, { method: 'POST', body: { user_id: grantUser.trim(), role: grantRole, reason: grantReason.trim() } }); if (current(commandTarget)) { setGrantUser(''); setAccessData(grants); await loadDetail(); setNotice('明确历史授权已保存，仍需同时具有当前账套权限。'); } })}>保存明确历史授权</button>{accessData && <div className="legacy-migration-access-grants"><h3>当前明确历史授权</h3>{accessData.grants.map(grant => <article key={grant.user_id}><div><strong>{grant.name}</strong><small>{grant.role === 'manager' ? '维护迁移资料' : grant.role === 'reviewer' ? '独立验收历史' : '查看历史资料'}</small></div><button className="text-button" disabled={busy || !grantReason.trim()} onClick={() => void revokeAccess(grant.user_id)}>撤销历史授权</button></article>)}</div>}</details>}
        </section>}
        <footer className="legacy-migration-pagefooter"><span><ShieldCheck size={14} />企业范围独立核验 · 当前凭证继续会计复核后导出</span><div><button className="button secondary small" disabled={busy || dirty || step === 0} onClick={() => setStep(value => Math.max(0, value - 1))}><ArrowLeft size={15} />上一步</button><button className="button secondary small" disabled={busy || dirty || step === 4} onClick={() => setStep(value => Math.min(4, value + 1))}>下一步<ArrowRight size={15} /></button></div></footer>
      </>}
    </>}
    {focus && <EvidenceDialog focus={focus} files={detail?.files ?? []} busy={working} onClose={() => setFocus(null)} onOriginal={getOriginal} onMapping={() => { if (!busy && !dirty) { setFocus(null); setStep(2); } }} onUpload={() => { if (!busy && !dirty) { setFocus(null); setStep(0); } }} />}
  </main>;
}

function Pagination({ total, offset, hasMore, disabled, onPage }: { total: number; offset: number; hasMore: boolean; disabled: boolean; onPage: (offset: number) => void }) {
  return <div className="legacy-migration-pagination"><span>共 {total} 项 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span><button className="button secondary small" disabled={disabled || offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))}>上一页</button><button className="button secondary small" disabled={disabled || !hasMore} onClick={() => onPage(offset + PAGE_SIZE)}>下一页</button></div>;
}
function FileList({ files, busy, onOriginal, onMapping }: { files: MigrationFile[]; busy: boolean; onOriginal: (id: string) => Promise<void>; onMapping?: (file: MigrationFile) => Promise<void> }) {
  return <div className="legacy-migration-files"><h3>已保留的原始资料 <span>{files.length}</span></h3>{files.length ? files.map(file => <article key={file.id}><FileSpreadsheet size={20} /><div><strong>{file.filename}</strong><small>{MIGRATION_CATEGORIES[file.category]} · {Math.ceil(file.size_bytes / 1024)} KB{file.row_count === undefined ? '' : ` · ${file.row_count} 行`}</small></div><span className="badge pending">{file.superseded ? '已由修订版替代' : migrationNeedsColumnConfirmation(file) ? '列对应待确认' : '原件已保留'}</span>{onMapping && !file.superseded && /\.(csv|xlsx)$/i.test(file.filename) && <button className="text-button" disabled={busy} onClick={() => void onMapping(file)}>{file.mapping?.confirmed ? '查看原表列' : '确认原表列'}</button>}<button className="text-button" disabled={busy} onClick={() => void onOriginal(file.id)}><Download size={14} />原件</button></article>) : <p className="legacy-migration-help">尚未上传资料。原文件保留后可以离开，再回来继续。</p>}</div>;
}
function Jobs({ jobs, busy, manage, hasReason, onAction }: { jobs: MigrationJob[]; busy: boolean; manage: boolean; hasReason: boolean; onAction: (job: MigrationJob, action: 'retry' | 'cancel') => Promise<void> }) {
  return <div className="legacy-migration-jobs">{jobs.map(job => { const progress = job.progress_total ? Math.min(100, Math.max(0, (job.progress_done ?? 0) * 100 / job.progress_total)) : Math.min(100, Math.max(0, job.progress ?? 0)); return <article key={job.id}><div className="legacy-migration-job-title"><strong>{job.kind === 'validate' ? '完整账务校验' : '原始资料解析'}</strong><span className={`badge ${['succeeded', 'completed'].includes(job.state) ? 'approved' : ['failed', 'interrupted'].includes(job.state) ? 'exception' : 'pending'}`}>{migrationState(job.state)}</span><small>{job.attempt ? `第 ${job.attempt} 次执行` : ''}</small></div><progress aria-label={`${job.kind === 'validate' ? '校验' : '解析'}任务进度`} value={progress} max={100} /><p>{job.progress_total ? `${job.progress_done ?? 0} / ${job.progress_total}` : migrationActive(job) ? '正在后台处理，页面关闭后仍可恢复' : migrationState(job.state)}</p>{(job.error_message || job.error) && <p className="inline-error">{job.error_message ?? job.error}</p>}<div className="legacy-migration-actions">{manage && (job.permissions?.retry ?? ['failed', 'interrupted'].includes(job.state)) && <button className="button secondary small" disabled={busy || !hasReason} onClick={() => void onAction(job, 'retry')}>重新排队</button>}{manage && (job.permissions?.cancel ?? migrationActive(job)) && <button className="text-button" disabled={busy || !hasReason} onClick={() => void onAction(job, 'cancel')}>取消此任务</button>}</div></article>; })}</div>;
}
function ColumnMapper({ kind, headers, mapping, confirmed, disabled, onMapping, onConfirm, multiplePeriods }: { kind: MigrationRecordKind; headers: string[]; mapping: MigrationColumnMapping; confirmed: boolean; disabled: boolean; onMapping: (value: MigrationColumnMapping) => void; onConfirm: (value: boolean) => void; multiplePeriods: boolean }) {
  const error = migrationColumnError(kind, mapping, multiplePeriods);
  function setColumn(field: string, value: string) {
    const columns = { ...mapping.columns };
    if (!value.trim()) delete columns[field]; else columns[field] = /^\d+$/.test(value) ? Number(value) : value;
    onMapping({ ...mapping, columns });
  }
  function changeBalance(mode: 'net_debit' | 'split') {
    const columns = { ...mapping.columns };
    for (const field of mode === 'split' ? ['opening', 'closing'] : ['opening_debit', 'opening_credit', 'closing_debit', 'closing_credit']) delete columns[field];
    onMapping({ ...mapping, columns, balance_mode: mode });
  }
  function statusValues(status: 'posted' | 'unposted' | 'void', value: string) {
    const values = Object.fromEntries(Object.entries(mapping.status_values ?? {}).filter(([, target]) => target !== status));
    for (const original of value.split(/[，,]/).map(text => text.trim()).filter(Boolean)) values[original] = status;
    onMapping({ ...mapping, status_values: values });
  }
  return <section className="legacy-migration-columnmapper"><h3>确认原表每一列的含义</h3><p>系统只建议明确表头；Excel 无法预览时填列序号（从 0 开始），或完整列名。金额保留原字符串交给后台精确校验。</p>
    {kind === 'balances' && <label>余额口径<select aria-label="旧账余额金额口径" value={mapping.balance_mode ?? 'net_debit'} disabled={disabled} onChange={event => changeBalance(event.target.value as 'net_debit' | 'split')}><option value="net_debit">净余额：借方正数，贷方负数</option><option value="split">借方余额、贷方余额分别列示</option></select></label>}
    <div className="legacy-migration-columnfields">{migrationFields(kind, mapping).map(field => <label key={field.key}><span>{field.label}{field.required && <small>必选</small>}</span>{headers.length ? <select aria-label={`旧账对应${field.label}`} value={mapping.columns[field.key] ?? ''} disabled={disabled} onChange={event => setColumn(field.key, event.target.value)}><option value="">不对应</option>{headers.map((header, index) => <option value={index} key={index}>{header} · 第 {index + 1} 列</option>)}</select> : <input aria-label={`旧账对应${field.label}`} value={mapping.columns[field.key] ?? ''} placeholder="列名或从 0 开始的列序号" disabled={disabled} onChange={event => setColumn(field.key, event.target.value)} />}</label>)}</div>
    <div className="legacy-migration-formgrid"><label>原表日期格式<select aria-label="旧账原表日期格式" value={mapping.date_format} disabled={disabled} onChange={event => onMapping({ ...mapping, date_format: event.target.value as MigrationColumnMapping['date_format'] })}><option value="ymd">年-月-日</option><option value="dmy">日/月/年</option><option value="mdy">月/日/年</option><option value="excel_serial">Excel 日期序号</option></select></label><label className="legacy-migration-check"><input type="checkbox" disabled={disabled} checked={mapping.allow_grouping} onChange={event => onMapping({ ...mapping, allow_grouping: event.target.checked })} />金额中的逗号表示千位分隔符</label></div>
    {kind === 'vouchers' && <details className="legacy-migration-status-mapping"><summary>原凭证状态对应</summary><p>“已审核”不等于“已记账”。只有核对原软件含义后再对应，未知状态会阻断接管；原状态始终保留。</p><div className="legacy-migration-columnfields">{([['posted', '已记账'], ['unposted', '未记账'], ['void', '作废']] as const).map(([status, label]) => <label key={status}>{label}在原表中的状态<input aria-label={`旧账${label}原状态`} disabled={disabled} value={Object.entries(mapping.status_values ?? {}).filter(([, value]) => value === status).map(([key]) => key).join(',')} placeholder={`默认支持 ${status} / ${label}`} onChange={event => statusValues(status, event.target.value)} /></label>)}</div></details>}
    {error && <p className="legacy-migration-help">{error}</p>}<label className="legacy-migration-check"><input aria-label="确认旧账原表列对应" type="checkbox" disabled={disabled || !!error} checked={confirmed} onChange={event => onConfirm(event.target.checked)} />已核对列对应、金额和日期格式，保留原表供后续复核。</label>
  </section>;
}
function ValidationSummary({ validation }: { validation: NonNullable<MigrationDetail['latest_validation']> }) {
  const summary = validation.summary ?? {};
  const counts = typeof summary.counts === 'object' && summary.counts !== null ? summary.counts as Record<string, number> : {};
  const totals = (typeof summary.totals === 'object' && summary.totals !== null ? summary.totals : validation.totals ?? {}) as Record<string, unknown>;
  const labels: Record<string, string> = { account: '科目', auxiliary: '辅助档案', voucher: '凭证', balance: '余额记录', open_item: '未结往来', deduplicated_count: '相同记录跳过', raw_record_count: '原始记录数', posted_debit: '已记账借方合计', posted_credit: '已记账贷方合计', open_receivable: '未结应收合计', open_payable: '未结应付合计', cutover_net_balance: '接管借贷净额差' };
  return <details className="legacy-migration-summary"><summary>完整校验数量与金额依据</summary><dl>{Object.entries(counts).map(([key, value]) => <div key={`count:${key}`}><dt>{labels[key] ?? key}</dt><dd>{value}</dd></div>)}{Object.entries(totals).map(([key, value]) => <div key={`total:${key}`}><dt>{labels[key] ?? key}</dt><dd>{migrationMoney(value, /_minor$|_cents$/.test(key))}</dd></div>)}{['deduplicated_count', 'raw_record_count'].filter(key => summary[key] !== undefined).map(key => <div key={key}><dt>{labels[key]}</dt><dd>{rawDisplay(summary[key])}</dd></div>)}</dl>{validation.coverage && <p className="legacy-migration-help">历史范围：{Array.isArray(validation.coverage.periods) ? validation.coverage.periods.join('、') : '以当前项目范围为准'} · {validation.coverage.complete ? '完整性核对通过' : '完整性仍需核对'}。</p>}</details>;
}
function EvidenceDialog({ focus, files, busy, onClose, onOriginal, onMapping, onUpload }: { focus: { title: string; record?: MigrationRecord; issue?: MigrationIssue }; files: MigrationFile[]; busy: boolean; onClose: () => void; onOriginal: (id: string) => Promise<void>; onMapping: () => void; onUpload: () => void }) {
  const dialog = useRef<HTMLDivElement>(null); const issue = focus.issue; const record = focus.record;
  const fileId = issue?.file_id ?? record?.file_id ?? record?.source_file_id; const file = files.find(item => item.id === fileId);
  const sourcePosition = issue?.row_number ?? record?.row_number ?? record?.source_row;
  const positionLabel = sourcePosition ? `${file && /\.json$/i.test(file.filename) ? '原包记录' : '原表'}第 ${sourcePosition} ${file && /\.json$/i.test(file.filename) ? '项' : '行'}` : '原件位置由完整解析依据保留';
  const payload = record ? recordData(record) : issue?.details ?? {};
  const fields = migrationReadableFields(payload);
  const lines = migrationReadableLines(payload.lines);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const bodyOverflow = document.body.style.overflow, documentOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden'; document.documentElement.style.overflow = 'hidden'; dialog.current?.focus();
    return () => { document.body.style.overflow = bodyOverflow; document.documentElement.style.overflow = documentOverflow; if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  function keydown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); return; }
    if (event.key !== 'Tab') return;
    const nodes = dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), summary, [tabindex="0"]'); if (!nodes?.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  return <div className="modal-backdrop legacy-migration-evidence-backdrop"><div className="legacy-migration-evidence-dialog" role="dialog" aria-modal="true" aria-labelledby="legacy-evidence-title" ref={dialog} tabIndex={-1} onKeyDown={keydown}>
    <header><div><p className="eyebrow">历史原件 · 当前企业</p><h2 id="legacy-evidence-title">{focus.title}</h2></div><button className="icon-button" aria-label="关闭旧账依据" disabled={busy} onClick={onClose}><X size={20} /></button></header>
    <main>{issue && <><span className={`badge ${issue.severity === 'blocking' ? 'exception' : 'pending'}`}>{issue.severity === 'blocking' ? '阻断接管' : '会计复核'}</span><h3>{issue.message}</h3><p>{issue.next_action || '核对原表、修正资料或映射后，重新执行完整校验。'}</p></>}
      <section className="legacy-migration-evidence-source"><strong>{file?.filename ?? '历史归档记录'}</strong><p>{positionLabel}</p>{file && <button className="button secondary" disabled={busy} onClick={() => void onOriginal(file.id)}><Download size={16} />下载获授权原件</button>}</section>
      {fields.length > 0 && <dl className="legacy-migration-readable-facts" aria-label="历史凭证与档案事实">{fields.map(field => <div key={field.key}><dt>{field.label}</dt><dd>{field.text}</dd></div>)}</dl>}
      {lines.length > 0 && <section className="legacy-migration-readable-journal" aria-label="历史凭证分录"><h3>原始凭证分录 <small>{lines.length} 条</small></h3>{lines.map((line, index) => <article key={index}><div><span>第 {line.ordinal} 条</span><strong>科目 {line.accountCode}</strong></div><p>{line.description || '未填写摘要'}</p><dl><div><dt>借方金额</dt><dd>{line.debit}</dd></div><div><dt>贷方金额</dt><dd>{line.credit}</dd></div></dl>{line.auxiliaries.length > 0 && <div className="legacy-migration-readable-auxiliaries">{line.auxiliaries.map((auxiliary, i) => <p key={i}><span>{auxiliary.label}</span>{auxiliary.value}</p>)}</div>}</article>)}</section>}
      <details className="legacy-migration-raw-details"><summary>查看原始字段与定位</summary><p>保留解析后的原始字段、文件指纹与定位信息，便于追溯原件；上方中文显示不改写原始内容。</p><dl className="legacy-migration-evidence-fields">{Object.entries(payload).map(([key, value]) => <div key={key}><dt>{key}</dt><dd><pre>{rawDisplay(value)}</pre></dd></div>)}</dl></details>
    </main>
    <footer>{issue && <><button className="button secondary" disabled={busy} onClick={onMapping}>核对科目与辅助映射</button><button className="button secondary" disabled={busy} onClick={onUpload}>补充或修正原始资料</button></>}<button className="button primary" disabled={busy} onClick={onClose}>返回当前工作位置</button></footer>
  </div></div>;
}

export default LegacyMigrationWorkspace;
