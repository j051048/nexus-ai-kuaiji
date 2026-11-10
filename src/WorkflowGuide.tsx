import './workflow-guide.css';

export type WorkflowGuideStep = {
  id: string;
  label: string;
  state: 'done' | 'current' | 'upcoming' | 'blocked';
};

export type WorkflowGuidance = {
  title: string;
  steps: WorkflowGuideStep[];
  status: string;
  next: string;
  tone?: 'neutral' | 'warning' | 'success';
  checks?: readonly string[];
};

const stateLabels: Record<WorkflowGuideStep['state'], string> = {
  done: '已完成',
  current: '当前步骤',
  upcoming: '后续步骤',
  blocked: '当前待处理',
};

/** Read-only guidance. Existing forms and server permissions remain authoritative. */
export function WorkflowGuide({
  label,
  guide,
  compact = false,
  collapsed = false,
  detailsLabel = '查看复核步骤与说明',
}: {
  label: string;
  guide: WorkflowGuidance;
  compact?: boolean;
  collapsed?: boolean;
  detailsLabel?: string;
}) {
  const folded = collapsed && guide.tone !== 'warning';
  const content = (
    <>
      {!compact && <h3>{guide.title}</h3>}
      <div className="workflow-guide-message">
        <p>
          <strong>当前状态</strong>
          {guide.status}
        </p>
        <p>
          <strong>下一步</strong>
          {guide.next}
        </p>
      </div>
      {compact && !folded ? (
        <details>
          <summary>{detailsLabel}</summary>
          <h3>{guide.title}</h3>
          <ol aria-label="办理步骤">
            {guide.steps.map((step, index) => (
              <li
                key={step.id}
                className={`workflow-guide-step workflow-guide-step--${step.state}`}
                aria-current={['current', 'blocked'].includes(step.state) ? 'step' : undefined}
              >
                <span className="workflow-guide-number" aria-hidden="true">
                  {step.state === 'done' ? '✓' : index + 1}
                </span>
                <span>{step.label}</span>
                <span className="sr-only">，{stateLabels[step.state]}</span>
              </li>
            ))}
          </ol>
        </details>
      ) : (
        <ol aria-label="办理步骤">
          {guide.steps.map((step, index) => (
            <li
              key={step.id}
              className={`workflow-guide-step workflow-guide-step--${step.state}`}
              aria-current={['current', 'blocked'].includes(step.state) ? 'step' : undefined}
            >
              <span className="workflow-guide-number" aria-hidden="true">
                {step.state === 'done' ? '✓' : index + 1}
              </span>
              <span>{step.label}</span>
              <span className="sr-only">，{stateLabels[step.state]}</span>
            </li>
          ))}
        </ol>
      )}
      {!!guide.checks?.length && (
        <details>
          <summary>本步核对要点</summary>
          <ul>
            {guide.checks.map((check, index) => (
              <li key={index}>{check}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
  return (
    <section
      className={`workflow-guide workflow-guide--${guide.tone ?? 'neutral'} ${folded ? 'workflow-guide-collapsed' : ''}`}
      aria-label={label}
    >
      {folded ? (
        <details className="business-extra-guide">
          <summary>{detailsLabel}</summary>
          {content}
        </details>
      ) : (
        content
      )}
    </section>
  );
}
