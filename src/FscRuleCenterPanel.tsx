import { useEffect, useState } from 'react';
import { Building2, CheckCircle2, RotateCcw, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from './api';
import { Spinner } from './AppPrimitives';
import {
  ALL_INDUSTRY_OPTIONS,
  readCompanyIndustryMap,
  resolveCompanyIndustry,
  saveCompanyIndustry,
  type FscIndustryKind,
} from './fsc-industry';
import {
  DEFAULT_TRIAGE_POLICY,
  readTriagePolicy,
  saveTriagePolicy,
  type FscTriagePolicy,
} from './fsc-triage';
import type { Session } from './types';
import './fsc-rule-center.css';

interface CompanyRow {
  id: string;
  name: string;
  status?: string;
}

export default function FscRuleCenterPanel({ session }: { session: Session }) {
  const [companies, setCompanies] = useState<CompanyRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [industryMap, setIndustryMap] = useState<Record<string, FscIndustryKind>>(() =>
    readCompanyIndustryMap(),
  );
  const [policy, setPolicy] = useState<FscTriagePolicy>(() => readTriagePolicy());
  const [savedNotice, setSavedNotice] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void api<{ companies: CompanyRow[] }>('/companies', session, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setCompanies(res.companies ?? []);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [session]);

  function handleIndustryChange(companyId: string, kind: FscIndustryKind) {
    saveCompanyIndustry(companyId, kind);
    setIndustryMap((prev) => ({ ...prev, [companyId]: kind }));
    setSavedNotice('子公司业态插件配置已保存，打开该公司总账与报税页即刻生效。');
  }

  function handleSavePolicy(next: FscTriagePolicy) {
    saveTriagePolicy(next);
    setPolicy(next);
    setSavedNotice('绿通合规包与高风险红线规则已保存，待办收件箱与批量复核台即刻生效。');
  }

  function handleResetPolicy() {
    saveTriagePolicy(DEFAULT_TRIAGE_POLICY);
    setPolicy(DEFAULT_TRIAGE_POLICY);
    setSavedNotice('已恢复集团共享中心默认绿通与红线拦截规则。');
  }

  return (
    <div className="fsc-rule-center">
      {savedNotice && (
        <p className="fsc-rule-notice" role="status">
          <CheckCircle2 size={15} aria-hidden="true" />
          <span>{savedNotice}</span>
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <section className="fsc-rule-card" aria-label="子公司业态插件配置矩阵">
        <header className="fsc-rule-card-head">
          <Building2 size={18} aria-hidden="true" />
          <div>
            <h2>子公司业态档案配置（90% 通用主干 + 10% 业态动态插件）</h2>
            <p>
              为集团旗下各子公司指定对应行业业态；打开对应公司账套时自动加载该业态专属核算与涉税指引，切换其他公司自动隐藏、绝不串台。
            </p>
          </div>
        </header>
        {loading ? (
          <div className="loading-block" role="status">
            <Spinner />
            正在读取集团子公司档案…
          </div>
        ) : companies.length === 0 ? (
          <p className="field-help">当前暂无已开通子公司，请先在“子公司档案”标签页新建或开通。</p>
        ) : (
          <div className="fsc-industry-table-wrap">
            <table className="fsc-industry-table">
              <thead>
                <tr>
                  <th>子公司名称</th>
                  <th>当前生效业态插件</th>
                  <th>专属核算摘要</th>
                </tr>
              </thead>
              <tbody>
                {companies.map((company) => {
                  const resolved = resolveCompanyIndustry(company.id, company.name, industryMap);
                  return (
                    <tr key={company.id}>
                      <td>
                        <strong>{company.name}</strong>
                      </td>
                      <td>
                        <select
                          aria-label={`${company.name} 业态选择`}
                          value={resolved.kind}
                          onChange={(event) =>
                            handleIndustryChange(company.id, event.target.value as FscIndustryKind)
                          }
                        >
                          {ALL_INDUSTRY_OPTIONS.map((opt) => (
                            <option key={opt.kind} value={opt.kind}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <span className="fsc-rule-summary-text">{resolved.summary}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="fsc-rule-card" aria-label="绿通合规包与高风险红线规则配置">
        <header className="fsc-rule-card-head">
          <ShieldCheck size={18} aria-hidden="true" />
          <div>
            <h2>绿通合规包阈值与高风险红线拦截规则</h2>
            <p>
              满足绿通条件的小额真票单据自动进入“绿通合规包”供主管一键批量放行；命中下方任一红线则强制标红拦截、须人工逐笔严审。
            </p>
          </div>
        </header>

        <div className="fsc-policy-grid">
          <label className="fsc-policy-amount">
            <span>绿通合规包单笔最高金额（人民币元）</span>
            <input
              type="number"
              min={100}
              max={500000}
              step={100}
              value={policy.greenMaxYuan}
              onChange={(event) => {
                const n = Number(event.target.value);
                if (Number.isFinite(n) && n >= 100 && n <= 500000) {
                  handleSavePolicy({ ...policy, greenMaxYuan: Math.round(n) });
                }
              }}
            />
            <small>超过此金额的单据自动进入高风险拦截队列，禁止自动打包批审。</small>
          </label>

          <label className="fsc-policy-toggle">
            <input
              type="checkbox"
              checked={policy.blockNewCounterparty}
              onChange={(event) =>
                handleSavePolicy({ ...policy, blockNewCounterparty: event.target.checked })
              }
            />
            <span>
              <strong>首次出现的收款账号强制红线拦截</strong>
              <small>核对合同公章与银行开户信息无误后，方可由人工复核放行。</small>
            </span>
          </label>

          <label className="fsc-policy-toggle">
            <input
              type="checkbox"
              checked={policy.blockCrossPeriod}
              onChange={(event) =>
                handleSavePolicy({ ...policy, blockCrossPeriod: event.target.checked })
              }
            />
            <span>
              <strong>跨期发票、无票白条与疑似重报强制拦截</strong>
              <small>命中跨期、缺票或重复发票号特征时自动标红提示具体原因。</small>
            </span>
          </label>

          <label className="fsc-policy-toggle">
            <input
              type="checkbox"
              checked={policy.requireOaRefHint}
              onChange={(event) =>
                handleSavePolicy({ ...policy, requireOaRefHint: event.target.checked })
              }
            />
            <span>
              <strong>审批环节醒目提示核对原办公审批单号</strong>
              <small>适配集团极简衔接模式：看清原办公系统签批结果后一键放行。</small>
            </span>
          </label>
        </div>

        <footer className="fsc-policy-foot">
          <span className="fsc-policy-safe-note">
            <ShieldCheck size={15} aria-hidden="true" />
            断网纯手工保底策略已常驻开启：即使云端接口离线，全站所有填单、制单、复核、对账与结账均可纯手工独立完成。
          </span>
          <button type="button" className="button secondary small" onClick={handleResetPolicy}>
            <RotateCcw size={14} aria-hidden="true" />
            恢复默认规则
          </button>
        </footer>
      </section>
    </div>
  );
}
