import { useEffect, useState } from 'react';
import {
  ArrowRight,
  CalendarDays,
  ClipboardList,
  RefreshCw,
  Save,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { Spinner } from './AppPrimitives';
import { ServicePlanMaterials } from './ServicePlanMaterials';
import { useServicePlanData } from './service-plan-data';
import { servicePlanAssignmentIssue } from './service-plan-types';
import type { ServicePlanCandidate, ServicePlanOverview } from './service-plan-types';
import type { Session } from './types';
import { serviceJourney, type ServiceJourneyTarget } from './service-journey';
import './service-plan.css';

export interface ServicePlanPanelProps {
  session: Session;
  bookId: string;
  companyId: string;
  companyName: string;
  period: string;
  revision?: number;
  onChanged?: () => void;
  onBusyChange?: (busy: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onOpenStage?: (target: ServiceJourneyTarget) => void;
}

export function ServicePlanPanel({
  session,
  bookId,
  companyId,
  companyName,
  period,
  revision = 0,
  onChanged,
  onBusyChange,
  onDirtyChange,
  onOpenStage,
}: ServicePlanPanelProps) {
  const path = `/books/${encodeURIComponent(bookId)}/service-plan`;
  const resource = useServicePlanData<ServicePlanOverview>(session, path, revision);
  const [planDirty, setPlanDirty] = useState(false);
  const [materialsDirty, setMaterialsDirty] = useState(false);
  const [materialsBusy, setMaterialsBusy] = useState(false);
  const [materialRevision, setMaterialRevision] = useState(0);
  const value = resource.data;
  const matching =
    value?.book.id === bookId && value.book.company_id === companyId && value.book.period === period
      ? value
      : null;
  useEffect(() => {
    if (!matching) {
      setPlanDirty(false);
      setMaterialsDirty(false);
      setMaterialsBusy(false);
    }
  }, [matching]);
  useEffect(() => {
    onBusyChange?.(resource.busy || materialsBusy);
    return () => onBusyChange?.(false);
  }, [resource.busy, materialsBusy, onBusyChange]);
  useEffect(() => {
    onDirtyChange?.(planDirty || materialsDirty);
    return () => onDirtyChange?.(false);
  }, [planDirty, materialsDirty, onDirtyChange]);
  const dirty = planDirty || materialsDirty;
  const journey = serviceJourney(
    matching,
    { bookId, companyId, period },
    {
      loading: resource.loading,
      busy: resource.busy || materialsBusy,
      dirty,
      revoked: resource.revoked,
      enabled: !!onOpenStage,
    },
  );
  function refresh() {
    if (!dirty || window.confirm('服务单或资料说明尚未保存，刷新会放弃本次编辑。是否继续？')) {
      setMaterialRevision((value) => value + 1);
      void resource.load();
    }
  }
  return (
    <section className="service-plan-workspace" aria-label="月度服务单">
      <header className="service-plan-heading">
        <div>
          <p className="eyebrow">CLIENT SERVICE / MONTHLY PLAN</p>
          <h1>{companyName} · 月度服务单</h1>
          <p>
            <CalendarDays size={15} />
            {period} <span>每项资料、每次交付都有明确责任。</span>
          </p>
        </div>
        <button
          type="button"
          className="button secondary small"
          disabled={resource.loading || resource.busy || materialsBusy}
          onClick={refresh}
        >
          <RefreshCw size={14} />
          刷新服务单
        </button>
      </header>
      {resource.error && (
        <p className="inline-error" role="alert">
          {resource.error}
        </p>
      )}
      {resource.notice && (
        <p className="service-notice" role="status">
          {resource.notice}
        </p>
      )}
      {resource.loading && (
        <p className="loading-block">
          <Spinner />
          正在核对账套与服务分工…
        </p>
      )}
      {value && !matching && (
        <p className="inline-error" role="alert">
          服务单与当前企业、期间或账套不一致，请返回客户列表重新进入。
        </p>
      )}
      {matching && (
        <>
          {matching.book.period_status === 'closed' && (
            <p className="service-notice" role="status">
              该会计期间已锁定，服务单和资料仅供查阅；如需补正，请按已批准的期间更正流程处理。
            </p>
          )}
          <div className="service-scope">
            <ShieldCheck size={16} />
            <span>
              {matching.book.name} · {matching.book.period} ·
              分工只面向当前已授权人员，指派不会新增数据访问权限。
            </span>
          </div>
          <nav className="service-journey" aria-label="本月办理路径">
            <div className="service-journey-heading">
              <h2>沿着这三步继续办理</h2>
              <p>保留当前企业、账套和月份；每一环节以实际记录为准。</p>
            </div>
            <ol>
              {journey.map((step, index) => (
                <li key={step.view}>
                  <span className="service-journey-number" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div>
                    <h3>{step.title}</h3>
                    <p>{step.description}</p>
                    {step.reason && <small>{step.reason}</small>}
                    <button
                      type="button"
                      className="button secondary"
                      data-service-stage={step.view}
                      disabled={step.disabled}
                      onClick={() => {
                        if (!step.disabled) onOpenStage?.(step.target);
                      }}
                    >
                      {step.action}
                      <ArrowRight size={14} aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ol>
          </nav>
          <ServicePlanEditor
            key={`${session.scope_key}:${bookId}:${matching.plan?.version ?? 0}:${resource.reset}`}
            overview={matching}
            busy={resource.busy}
            onDirtyChange={setPlanDirty}
            onSave={async (body) => {
              const saved = await resource.mutate(
                path,
                body,
                '本月负责人、交付期限与服务说明已保存。',
                'PUT',
              );
              if (saved) onChanged?.();
            }}
            onTemplate={async (body) => {
              const saved = await resource.mutate(
                `/books/${encodeURIComponent(bookId)}/material-templates`,
                body,
                '资料模板已保存；请核对后明确生成本月清单。',
              );
              if (saved) onChanged?.();
            }}
            onGenerate={async (templateId, expectedVersion, reason) => {
              const saved = await resource.mutate(
                `/books/${encodeURIComponent(bookId)}/material-templates/${encodeURIComponent(templateId)}/generate`,
                { expected_version: expectedVersion, reason },
                '本月资料请求已生成；重复生成同一模板不会重复创建。',
              );
              if (saved) {
                setMaterialRevision((value) => value + 1);
                onChanged?.();
              }
            }}
          />
          <ServicePlanMaterials
            session={session}
            bookId={bookId}
            companyId={companyId}
            period={period}
            revision={revision + materialRevision}
            onBusyChange={setMaterialsBusy}
            onDirtyChange={setMaterialsDirty}
            onChanged={onChanged}
          />
        </>
      )}
    </section>
  );
}

function CandidateSelect({
  label,
  value,
  candidates,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  candidates: ServicePlanCandidate[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const missing = !!value && !candidates.some((item) => item.user_id === value);
  return (
    <label>
      {label}
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">尚未指派</option>
        {missing && (
          <option value={value} disabled>
            原指派人员权限已变更，请重新选择
          </option>
        )}
        {candidates.map((item) => (
          <option key={item.user_id} value={item.user_id}>
            {item.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function ServicePlanEditor({
  overview,
  busy,
  onDirtyChange,
  onSave,
  onTemplate,
  onGenerate,
}: {
  overview: ServicePlanOverview;
  busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (body: object) => Promise<void>;
  onTemplate: (body: object) => Promise<void>;
  onGenerate: (id: string, version: number, reason: string) => Promise<void>;
}) {
  const { plan, candidates } = overview;
  const [lead, setLead] = useState(plan?.lead_user_id ?? '');
  const [reviewer, setReviewer] = useState(plan?.reviewer_user_id ?? '');
  const [backup, setBackup] = useState(plan?.backup_user_id ?? '');
  const [contact, setContact] = useState(plan?.contact_user_id ?? '');
  const [due, setDue] = useState(plan?.due_on ?? `${overview.book.period}-28`);
  const [notes, setNotes] = useState(plan?.notes ?? '');
  const [reason, setReason] = useState('');
  const [templateName, setTemplateName] = useState('');
  const [templateLines, setTemplateLines] = useState('');
  const [templateAssignee, setTemplateAssignee] = useState('');
  const [templateDay, setTemplateDay] = useState('10');
  const [templateMonthOffset, setTemplateMonthOffset] = useState('1');
  const [templateReason, setTemplateReason] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [generateReason, setGenerateReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState('');
  const editable = overview.permissions.manage && !busy;
  const template = overview.templates.find((item) => item.id === templateId);
  const changes =
    lead !== (plan?.lead_user_id ?? '') ||
    reviewer !== (plan?.reviewer_user_id ?? '') ||
    backup !== (plan?.backup_user_id ?? '') ||
    contact !== (plan?.contact_user_id ?? '') ||
    due !== (plan?.due_on ?? `${overview.book.period}-28`) ||
    notes !== (plan?.notes ?? '');
  useEffect(() => {
    onDirtyChange(
      changes ||
        !!reason ||
        !!templateName ||
        !!templateLines ||
        !!templateReason ||
        !!generateReason,
    );
  }, [changes, reason, templateName, templateLines, templateReason, generateReason, onDirtyChange]);
  const assignmentIssue = servicePlanAssignmentIssue(lead, reviewer, candidates);
  return (
    <div className="service-plan-columns">
      <section className="service-plan-card">
        <header>
          <Users size={18} />
          <div>
            <h2>人员与交付期限</h2>
            <p>{plan ? `本月版本 ${plan.version}` : '尚未建立本月分工'} · 不同账套分别管理</p>
          </div>
        </header>
        <form
          className="service-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (assignmentIssue) {
              setError(assignmentIssue);
              return;
            }
            void onSave({
              expected_version: plan?.version ?? 0,
              lead_user_id: lead || null,
              reviewer_user_id: reviewer || null,
              backup_user_id: backup || null,
              contact_user_id: contact || null,
              due_on: due,
              notes: notes.trim(),
              reason: reason.trim(),
            });
          }}
        >
          <div className="service-fields">
            <CandidateSelect
              label="主办会计"
              value={lead}
              candidates={candidates.filter((item) => item.can_lead)}
              disabled={!editable}
              onChange={setLead}
            />
            <CandidateSelect
              label="独立复核人"
              value={reviewer}
              candidates={candidates.filter((item) => item.can_review)}
              disabled={!editable}
              onChange={setReviewer}
            />
            <CandidateSelect
              label="备岗会计"
              value={backup}
              candidates={candidates.filter((item) => item.can_lead)}
              disabled={!editable}
              onChange={setBackup}
            />
            <CandidateSelect
              label="客户资料联系人"
              value={contact}
              candidates={candidates.filter((item) => item.can_contact)}
              disabled={!editable}
              onChange={setContact}
            />
          </div>
          <label>
            本月交付截止日期
            <input
              aria-label="本月交付截止日期"
              type="date"
              required
              value={due}
              disabled={!editable}
              onChange={(event) => setDue(event.target.value)}
            />
          </label>
          <label>
            服务说明
            <textarea
              aria-label="月度服务说明"
              maxLength={1000}
              value={notes}
              disabled={!editable}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="约定本月服务范围、客户关注事项和交接要求"
            />
          </label>
          {overview.permissions.manage && (
            <>
              <label>
                分工变更原因
                <textarea
                  aria-label="服务单变更原因"
                  minLength={3}
                  maxLength={1000}
                  required
                  value={reason}
                  disabled={busy}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="记录本月安排或本次调整的原因"
                />
              </label>
              {(assignmentIssue || error) && (
                <p role="alert" className="inline-error">
                  {assignmentIssue || error}
                </p>
              )}
              <button
                type="submit"
                className="button primary"
                disabled={!editable || reason.trim().length < 3 || !!assignmentIssue || !due}
              >
                <Save size={15} />
                保存本月服务单
              </button>
            </>
          )}
        </form>
      </section>
      <section className="service-plan-card">
        <header>
          <ClipboardList size={18} />
          <div>
            <h2>周期资料模板</h2>
            <p>模板沿用长期账套，本月资料须明确生成。</p>
          </div>
        </header>
        {overview.templates.length ? (
          <form
            className="service-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (template && confirmed && generateReason.trim().length >= 3)
                void onGenerate(template.id, template.version, generateReason.trim());
            }}
          >
            <label>
              选择账套资料模板
              <select
                aria-label="选择资料模板"
                value={templateId}
                disabled={!editable}
                onChange={(event) => {
                  setTemplateId(event.target.value);
                  setConfirmed(false);
                }}
              >
                <option value="">选择模板并核对内容</option>
                {overview.templates.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name} · v{item.version}
                  </option>
                ))}
              </select>
            </label>
            {template && (
              <ul className="service-template-preview">
                {template.items.map((item) => (
                  <li key={item.key}>
                    <strong>{item.title}</strong>
                    <span>
                      {item.due_month_offset === 0 ? '当月' : '次月'} {item.due_day} 日前 ·{' '}
                      {item.required ? '必需' : '按需'} ·{' '}
                      {candidates.find((candidate) => candidate.user_id === item.assignee_user_id)
                        ?.name ?? '使用本月资料联系人；生成时核验权限'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {overview.permissions.manage && (
              <>
                <label>
                  本月生成说明
                  <textarea
                    aria-label="本月清单生成说明"
                    value={generateReason}
                    minLength={3}
                    maxLength={1000}
                    required
                    disabled={busy}
                    onChange={(event) => setGenerateReason(event.target.value)}
                  />
                </label>
                <label className="service-checkbox">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    disabled={busy || !template}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  已核对本月适用范围、资料责任人与截止日期
                </label>
                <button
                  className="button primary"
                  type="submit"
                  disabled={
                    !editable || !confirmed || !template || generateReason.trim().length < 3
                  }
                >
                  生成 {overview.book.period} 资料清单
                </button>
              </>
            )}
          </form>
        ) : (
          <p className="service-empty-copy">账套尚无周期资料模板，请根据客户实际业务创建。</p>
        )}
        {overview.permissions.manage && (
          <details className="service-template-create">
            <summary>新建账套资料模板</summary>
            <form
              className="service-form"
              onSubmit={(event) => {
                event.preventDefault();
                const titles = templateLines
                  .split('\n')
                  .map((line) => line.trim())
                  .filter(Boolean);
                if (
                  !titles.length ||
                  titles.length > 20 ||
                  titles.some((title) => title.length > 120)
                ) {
                  setError('请输入 1–20 项资料，每行一项且不超过 120 字。');
                  return;
                }
                void onTemplate({
                  name: templateName.trim(),
                  items: titles.map((title) => ({
                    key: crypto.randomUUID(),
                    title,
                    description: '',
                    required: true,
                    due_day: Number(templateDay),
                    due_month_offset: Number(templateMonthOffset),
                    assignee_user_id: templateAssignee || null,
                  })),
                  reason: templateReason.trim(),
                });
              }}
            >
              <label>
                模板名称
                <input
                  aria-label="资料模板名称"
                  value={templateName}
                  required
                  maxLength={120}
                  disabled={busy}
                  onChange={(event) => setTemplateName(event.target.value)}
                  placeholder="例如：服务业企业月度资料"
                />
              </label>
              <label>
                资料项目（每行一项）
                <textarea
                  aria-label="模板资料项目"
                  value={templateLines}
                  required
                  maxLength={4000}
                  disabled={busy}
                  onChange={(event) => setTemplateLines(event.target.value)}
                  placeholder={'银行对账单\n本月开销票据\n应收应付往来确认'}
                />
              </label>
              <div className="service-fields">
                <CandidateSelect
                  label="资料默认责任人"
                  value={templateAssignee}
                  candidates={candidates.filter((item) => item.can_contact)}
                  disabled={busy}
                  onChange={setTemplateAssignee}
                />
                <label>
                  每月资料截止日
                  <input
                    aria-label="每月资料截止日"
                    type="number"
                    value={templateDay}
                    min={1}
                    max={28}
                    required
                    disabled={busy}
                    onChange={(event) => setTemplateDay(event.target.value)}
                  />
                </label>
              </div>
              <label>
                资料截止月份
                <select
                  aria-label="资料截止月份"
                  value={templateMonthOffset}
                  disabled={busy}
                  onChange={(event) => setTemplateMonthOffset(event.target.value)}
                >
                  <option value="1">业务月份的次月</option>
                  <option value="0">业务当月</option>
                </select>
              </label>
              <small>未选择资料责任人时使用本月服务单的资料联系人；两处均未指派时无法生成。</small>
              <label>
                模板创建原因
                <textarea
                  aria-label="模板创建原因"
                  value={templateReason}
                  required
                  minLength={3}
                  maxLength={1000}
                  disabled={busy}
                  onChange={(event) => setTemplateReason(event.target.value)}
                />
              </label>
              <button
                type="submit"
                className="button secondary"
                disabled={
                  busy ||
                  !templateName.trim() ||
                  !templateLines.trim() ||
                  templateReason.trim().length < 3 ||
                  Number(templateDay) < 1 ||
                  Number(templateDay) > 28
                }
              >
                保存资料模板
              </button>
            </form>
          </details>
        )}
        <p className="service-boundary">
          “无发生”和“不适用”需记录依据并由独立人员验收；模板生成不会创建业务金额、会计分录或自动认定资料齐全。
        </p>
      </section>
    </div>
  );
}
