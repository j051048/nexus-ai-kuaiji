import { readinessLabels, readinessTone, type FilingReadiness } from './tax-readiness';

export function TaxReadinessList({ data }: { data: FilingReadiness | null }) {
  if (!data) return null;
  return (
    <section className="tax-readiness" aria-label="申报前清单" data-period={data.period}>
      <header>
        <h4>申报前清单 · {data.period}</h4>
        <span className={`tax-readiness-tone tone-${data.supported ? 'ok' : 'danger'}`}>
          {data.supported ? '有可用底稿口径' : '本期不能出底稿'}
        </span>
      </header>
      {!data.supported && data.blocked_reason && (
        <p className="tax-readiness-blocked" role="status">
          {data.blocked_reason}
        </p>
      )}
      <ul className="tax-readiness-list">
        {data.kinds.map((kind) => (
          <li key={`${kind.kind}-${kind.readiness}`} data-state={kind.readiness}>
            <div className="tax-readiness-head">
              <strong>{kind.label}</strong>
              <span className={`tax-readiness-tone tone-${readinessTone(kind.readiness)}`}>
                {readinessLabels[kind.readiness]}
              </span>
            </div>
            {kind.workpaper_version !== null && (
              <p className="tax-readiness-meta">
                底稿 V{kind.workpaper_version} · {kind.reviewed ? '已独立复核' : '未复核'} ·{' '}
                {kind.current ? '与当前来源一致' : '与当前来源不一致'}
              </p>
            )}
            {kind.reason && <p className="tax-readiness-meta">{kind.reason}</p>}
            {kind.next_action && <p className="tax-readiness-next">下一步：{kind.next_action}</p>}
          </li>
        ))}
      </ul>
      <p className="tax-readiness-note">
        这里只反映内部底稿与复核状态；外部办理结果需人工登记，登记前一律视为「未申报」。
      </p>
    </section>
  );
}
