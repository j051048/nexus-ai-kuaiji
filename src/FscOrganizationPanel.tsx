import { useEffect, useState } from 'react';

import { api } from './api';
import type { Session } from './types';

type Plan = {
  version: number;
  lead_user_id: string | null;
  reviewer_user_id: string | null;
  backup_user_id: string | null;
  contact_user_id: string | null;
  due_on: string;
  notes: string;
};

type Book = {
  book_id: string;
  book_name: string;
  period: string;
  status: string | null;
  plan: Plan | null;
  book_roles: Record<string, string>;
  unplanned_access: string[];
};

type Warning = { kind: string; book_id: string; user_id?: string };
type Company = {
  company_id: string;
  company_name: string;
  client_status: string;
  books: Book[];
  warnings: Warning[];
};
type Person = { user_id: string; name: string; active: boolean; firm_role: string };
type Projection = {
  scope: string;
  company_count: number;
  people: Person[];
  companies: Company[];
  summary: { companies: number; books: number; warnings: number; unstaffed: number };
};

const WARNING: Record<string, string> = {
  missing_service_plan: '缺少服务计划',
  missing_lead: '未指定主办',
  lead_reviewer_same: '主办与复核为同一人',
  backup_without_book_access: '备援没有当前账套权限',
  unstaffed: '账套无人负责',
};

function personName(people: Person[], id: string | null) {
  if (!id) return '未安排';
  return people.find((person) => person.user_id === id)?.name ?? id;
}

export function FscOrganizationPanel({ session }: { session: Session }) {
  const [data, setData] = useState<Projection | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api<Projection>('/fsc/organization', session)
      .then((body) => {
        if (!alive) return;
        setError('');
        setData(body);
      })
      .catch((cause) => {
        if (!alive || cause?.name === 'AbortError') return;
        setError('组织与分工投影读取失败');
      });
    return () => {
      alive = false;
    };
  }, [session]);

  if (error)
    return (
      <p className="fsc-org-error" role="alert">
        {error}
      </p>
    );
  if (!data) return <p className="fsc-org-loading">正在读取组织与分工…</p>;
  return (
    <section className="fsc-org" aria-label="组织与分工投影">
      <header>
        <div>
          <h2>组织与分工</h2>
          <p>只读投影：显示服务计划、当前账套角色和缺口；指派不会授予权限，也不会改变复核责任。</p>
        </div>
        <strong>
          {data.company_count} 家企业 · {data.summary.warnings} 项缺口
        </strong>
      </header>
      <div className="fsc-org-summary">
        <span>账套 {data.summary.books}</span>
        <span>人员 {data.people.length}</span>
        <span>无人负责 {data.summary.unstaffed}</span>
        <span>
          未登记计划{' '}
          {data.companies.reduce(
            (count, company) => count + company.books.filter((book) => !book.plan).length,
            0,
          )}
        </span>
      </div>
      <div className="fsc-org-list">
        {data.companies.map((company) => (
          <article key={company.company_id} className="fsc-org-company">
            <div className="fsc-org-company-head">
              <strong>{company.company_name}</strong>
              <span>
                {company.client_status === 'active' ? '服务中' : '已暂停'} · {company.books.length}{' '}
                套账
              </span>
            </div>
            {company.books.map((book) => (
              <div key={book.book_id} className="fsc-org-book">
                <div className="fsc-org-book-head">
                  <span>{book.book_name}</span>
                  <code>{book.period}</code>
                </div>
                {book.plan ? (
                  <div className="fsc-org-plan">
                    <span>计划 v{book.plan.version}</span>
                    <span>主办 {personName(data.people, book.plan.lead_user_id)}</span>
                    <span>复核 {personName(data.people, book.plan.reviewer_user_id)}</span>
                    <span>备援 {personName(data.people, book.plan.backup_user_id)}</span>
                    <span>截止 {book.plan.due_on}</span>
                  </div>
                ) : (
                  <p className="fsc-org-missing">尚未登记服务计划</p>
                )}
                <div className="fsc-org-roles">
                  {Object.entries(book.book_roles).map(([userId, role]) => (
                    <span key={userId}>
                      {personName(data.people, userId)} · {role}
                    </span>
                  ))}
                  {Object.keys(book.book_roles).length === 0 && <span>没有账套角色</span>}
                </div>
                {company.warnings
                  .filter((warning) => warning.book_id === book.book_id)
                  .map((warning) => (
                    <span key={`${warning.kind}-${warning.book_id}`} className="fsc-org-warning">
                      {WARNING[warning.kind] ?? warning.kind}
                    </span>
                  ))}
              </div>
            ))}
            {company.books.length === 0 && <p className="fsc-org-missing">尚未开通账套</p>}
          </article>
        ))}
      </div>
      <p className="fsc-org-note">
        投影不授予权限；新增或变更分工仍需在人员与授权中完成并留下审计记录。
      </p>
    </section>
  );
}
