import { useState } from 'react';
import { api } from './api';
import type { Session } from './types';
import type { LearningRule } from './learning-types';
import { readRuleMemory } from './rule-memory';
import { useScopedQuery } from './useScopedQuery';

export function RuleMemoryPanel({ session, rule }: { session: Session; rule: LearningRule }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="rule-memory" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>审核来源与适用范围</summary>
      {open && (
        <Memory
          key={`${session.scope_key}:${rule.id}:${rule.current_version}:${rule.state_version}`}
          session={session}
          rule={rule}
        />
      )}
    </details>
  );
}
function Memory({ session, rule }: { session: Session; rule: LearningRule }) {
  const expected = {
    firm_id: session.firm_id ?? '',
    company_id: rule.company_id,
    book_id: rule.book_id,
    period: rule.period,
    rule_id: rule.id,
    version: rule.current_version,
    state_version: rule.state_version,
  };
  const query = useScopedQuery(
    async (signal) =>
      readRuleMemory(
        await api(
          `/books/${encodeURIComponent(rule.book_id)}/learning/rules/${encodeURIComponent(rule.id)}/memory?expected_version=${rule.current_version}&expected_state_version=${rule.state_version}`,
          session,
          { signal },
        ),
        expected,
      ),
    JSON.stringify(expected),
  );
  if (query.loading) return <p role="status">正在核对当前审核来源…</p>;
  if (query.error)
    return (
      <div role="alert">
        <p>{query.error}</p>
        <button className="button secondary" onClick={query.reload}>
          重新核对依据
        </button>
      </div>
    );
  const value = query.data;
  if (!value) return null;
  const labels = {
    candidate: '规则候选，尚未发布',
    active: '已发布模板',
    blocked: '依据已失效',
    retired: '已停用',
  };
  return (
    <section aria-label="规则审核依据">
      <p>
        <strong>{labels[value.state]}</strong> · {rule.period} · v{rule.current_version}
      </p>
      <p>{value.boundary}</p>
      {value.unavailable.map((reason) => (
        <p key={reason} role="status">
          {reason}
        </p>
      ))}
      <p>可核对审核来源：{value.sources.length} 项</p>
      <ul>
        {value.sources.map((s) => (
          <li key={s.sample_id}>
            审核版本 {s.reviewed_version} · 原件 {s.original_hashes.length} 份
          </li>
        ))}
      </ul>
      <p>下一步：{value.next_action}</p>
      <details>
        <summary>追溯标识</summary>
        <p className="dcp-fingerprint">{value.fingerprint}</p>
        {value.sources.map((s) => (
          <p className="dcp-fingerprint" key={s.sample_id}>
            样例 {s.sample_id} · 凭证 {s.draft_id} · 复核人 {s.reviewed_by}
          </p>
        ))}
      </details>
    </section>
  );
}
