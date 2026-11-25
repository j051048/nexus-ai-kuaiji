import { Building2, CheckCircle2, Sparkles } from 'lucide-react';
import { resolveCompanyIndustry } from './fsc-industry';
import './industry-panel.css';

export function IndustryPanel({
  companyId,
  companyName,
  period,
}: {
  companyId?: string | null;
  companyName?: string | null;
  period?: string;
}) {
  const profile = resolveCompanyIndustry(companyId, companyName);
  // 通用商贸无需额外弹行业插件卡片，保持 90% 通用主干清爽；仅对 5 大特色业态动态挂载专属卡片
  if (profile.kind === 'general') return null;

  return (
    <section
      className="fsc-industry-panel"
      aria-label={`${profile.label}专属核算指引`}
      data-industry={profile.kind}
    >
      <div className="fsc-industry-head">
        <div className="fsc-industry-title">
          <span className="fsc-industry-icon">
            <Building2 size={16} aria-hidden="true" />
          </span>
          <div>
            <strong>
              {companyName || '当前子公司'} · {profile.label}专属核算插件
            </strong>
            <p>{profile.summary}</p>
          </div>
        </div>
        <span className="fsc-industry-badge">
          <Sparkles size={12} aria-hidden="true" />
          {profile.badge}
          {period ? ` · ${period}` : ''}
        </span>
      </div>
      <div className="fsc-industry-grid">
        <div>
          <h3>本业态核算要点（切换其他业态子公司自动隐藏）</h3>
          <ul>
            {profile.accountingFocus.map((item) => (
              <li key={item}>
                <CheckCircle2 size={13} aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>本业态涉税与月结提醒</h3>
          <ul>
            {profile.taxReminders.map((item) => (
              <li key={item}>
                <CheckCircle2 size={13} aria-hidden="true" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
          <div className="fsc-industry-kpis">
            <span>重点经营指标：</span>
            {profile.kpiHints.map((kpi) => (
              <b key={kpi}>{kpi}</b>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
