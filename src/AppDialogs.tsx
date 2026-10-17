import { useEffect, useState, type FormEvent } from 'react';
import { Building2, Plus } from 'lucide-react';
import { api, errorMessage } from './api';
import { Modal, Spinner } from './AppPrimitives';
import type { Session, Company } from './types';
export function NewBookModal({
  session,
  canCreateCompany,
  busy,
  onClose,
  onSubmit,
  serverError,
}: {
  session: Session;
  canCreateCompany: boolean;
  serverError?: string;
  busy: boolean;
  onClose: () => void;
  onSubmit: (name: string, period: string, companyId: string) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [period, setPeriod] = useState('2026-10');
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [companyName, setCompanyName] = useState('');
  const [taxId, setTaxId] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void api<{ companies: Company[] }>('/companies', session, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) {
          setCompanies(result.companies);
          if (!canCreateCompany) {
            setCompanyId(result.companies[0]?.id ?? '');
            setName(result.companies[0]?.name ?? '');
          }
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [session, canCreateCompany]);
  const submitting = busy || creating;
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || loading) return;
    setError('');
    setCreating(true);
    try {
      let target = companyId;
      if (!target) {
        if (!canCreateCompany || !companyName.trim()) return;
        const company = await api<Company>('/companies', session, {
          method: 'POST',
          body: { name: companyName.trim(), tax_id: taxId.trim() || undefined },
        });
        target = company.id;
        setCompanyId(target);
        setCompanies((current) => [...current, company]);
      }
      await onSubmit(name.trim(), period, target);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setCreating(false);
    }
  }
  return (
    <Modal
      error={error || serverError}
      title="创建客户账套"
      subtitle="先关联客户企业，再建立独立的来源与凭证空间"
      onClose={onClose}
      busy={submitting}
    >
      <form onSubmit={(event) => void submit(event)}>
        <div className="form-content">
          <label className="field-label" htmlFor="book-company">
            客户企业
          </label>
          <select
            id="book-company"
            value={companyId}
            disabled={submitting || loading}
            onChange={(event) => {
              setCompanyId(event.target.value);
              const company = companies.find((item) => item.id === event.target.value);
              setName(company?.name ?? companyName);
            }}
          >
            {canCreateCompany ? (
              <option value="">新建客户企业</option>
            ) : (
              <option value="">选择获授权企业</option>
            )}
            {companies.map((company) => (
              <option key={company.id} value={company.id}>
                {company.name}
              </option>
            ))}
          </select>
          {loading && <p className="field-help">正在载入客户企业…</p>}
          {!companyId && canCreateCompany && (
            <div className="new-company-fields">
              <label className="field-label" htmlFor="company-name">
                企业名称
              </label>
              <input
                id="company-name"
                autoFocus
                value={companyName}
                onChange={(event) => {
                  setCompanyName(event.target.value);
                  if (!name || name === companyName) setName(event.target.value);
                }}
                maxLength={120}
                placeholder="请输入企业完整名称"
                required
                disabled={submitting}
              />
              <label className="field-label" htmlFor="company-tax-id">
                统一社会信用代码（选填）
              </label>
              <input
                id="company-tax-id"
                value={taxId}
                onChange={(event) => setTaxId(event.target.value)}
                maxLength={30}
                placeholder="用于区分名称相近的客户企业"
                disabled={submitting}
              />
            </div>
          )}
          <label className="field-label" htmlFor="book-name">
            账套名称
          </label>
          <input
            id="book-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            placeholder="例如：企业名称 · 2026 年账套"
            required
            disabled={submitting}
          />
          <label className="field-label" htmlFor="book-period">
            会计期间
          </label>
          <input
            id="book-period"
            type="month"
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
            min="2000-01"
            max="2099-12"
            required
            disabled={submitting}
          />
          <div className="info-callout">
            <Building2 size={18} />
            <p>当前支持中国大陆企业及人民币账套。预置科目为验证示例，请由会计核对适用性。</p>
          </div>
        </div>
        <div className="modal-footer">
          <button
            className="button secondary"
            type="button"
            onClick={onClose}
            disabled={submitting}
          >
            取消
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={
              submitting ||
              loading ||
              !name.trim() ||
              !period ||
              (!companyId && (!canCreateCompany || !companyName.trim()))
            }
          >
            {submitting ? <Spinner /> : <Plus size={15} />}创建账套
          </button>
        </div>
      </form>
    </Modal>
  );
}
