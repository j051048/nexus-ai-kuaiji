import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { ReviewWorkspaceSession } from './review-session';

export function readReviewFilters(search: string) {
  const query = new URLSearchParams(search);
  const period = query.get('review_period') ?? query.get('period') ?? '';
  const template = query.get('review_template') ?? 'all';
  const risk = query.get('review_risk') ?? 'all';
  const offset = Number(query.get('review_offset') ?? 0);
  return {
    company: (query.get('review_company') ?? query.get('company') ?? '').slice(0, 200),
    period: /^\d{4}-(0[1-9]|1[0-2])$/.test(period) ? period : '',
    template: ['all', 'bank', 'reimbursement', 'procurement_expense', 'daily_cashflow'].includes(
      template,
    )
      ? template
      : 'all',
    risk: ['all', 'basic_clear', 'basic_exception'].includes(risk) ? risk : 'all',
    offset: Number.isSafeInteger(offset) && offset >= 0 && offset <= 1_000_000 ? offset : 0,
  };
}

export function useReviewFilters(remembered: ReviewWorkspaceSession | null) {
  const location = useLocation(),
    navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const initial =
    query.has('review_company') ||
    query.has('review_period') ||
    query.has('review_template') ||
    query.has('review_risk') ||
    query.has('review_offset') ||
    query.has('company') ||
    query.has('period') ||
    query.has('book')
      ? readReviewFilters(location.search)
      : (remembered ?? readReviewFilters(''));
  const [company, setCompany] = useState(initial.company);
  const [period, setPeriod] = useState(initial.period);
  const [template, setTemplate] = useState(initial.template);
  const [risk, setRisk] = useState(initial.risk);
  const [offset, setOffset] = useState(initial.offset);
  const lastLocation = useRef(location.key),
    own = useRef(''),
    applying = useRef(false);

  useEffect(() => {
    if (lastLocation.current === location.key) return;
    lastLocation.current = location.key;
    if (own.current === location.search) {
      own.current = '';
      return;
    }
    applying.current = true;
    const next = readReviewFilters(location.search);
    setCompany(next.company);
    setPeriod(next.period);
    setTemplate(next.template);
    setRisk(next.risk);
    setOffset(next.offset);
  }, [location.key, location.search]);
  useEffect(() => {
    if (applying.current) {
      applying.current = false;
      return;
    }
    if (location.pathname !== '/workspace/review') return;
    const next = new URLSearchParams(location.search);
    for (const [name, value] of Object.entries({
      review_company: company,
      review_period: period,
      review_template: template === 'all' ? '' : template,
      review_risk: risk === 'all' ? '' : risk,
      review_offset: offset ? String(offset) : '',
    })) {
      if (value) next.set(name, value);
      else next.delete(name);
    }
    const search = next.size ? '?' + next.toString() : '';
    if (search === location.search) return;
    own.current = search;
    void navigate({ pathname: location.pathname, search }, { replace: true });
  }, [company, period, template, risk, offset, location.pathname, location.search, navigate]);
  return {
    company,
    setCompany,
    period,
    setPeriod,
    template,
    setTemplate,
    risk,
    setRisk,
    offset,
    setOffset,
  };
}
