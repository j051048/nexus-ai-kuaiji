import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Save, TriangleAlert, X } from 'lucide-react';
import {
  normalizePolicyRequest,
  policyFromOverview,
  validatePolicyInput,
} from './operations-helpers';
import type { OperationsPolicy, OperationsPolicyInput } from './operations-types';

const thresholds: {
  key: keyof OperationsPolicyInput;
  label: string;
  unit: string;
  minimum: number;
  maximum: number;
}[] = [
  { key: 'failure_threshold', label: '连续失败次数', unit: '次', minimum: 1, maximum: 1000 },
  {
    key: 'failure_window_minutes',
    label: '失败观察窗口',
    unit: '分钟',
    minimum: 1,
    maximum: 10080,
  },
  { key: 'backlog_threshold', label: '积压份数', unit: '份', minimum: 1, maximum: 1000 },
  { key: 'backlog_age_minutes', label: '积压时间', unit: '分钟', minimum: 1, maximum: 10080 },
  { key: 'queue_wait_minutes', label: '排队等待时间', unit: '分钟', minimum: 1, maximum: 10080 },
  { key: 'worker_stale_seconds', label: '后台心跳时限', unit: '秒', minimum: 30, maximum: 3600 },
];
export function OperationsPolicyDialog({
  policy,
  companyName,
  busy,
  error,
  pricingReady,
  onClose,
  onSave,
}: {
  policy: OperationsPolicy;
  companyName: string;
  busy: boolean;
  error: string;
  pricingReady: boolean;
  onClose: () => void;
  onSave: (value: OperationsPolicyInput) => Promise<void>;
}) {
  const [input, setInput] = useState(() => policyFromOverview(policy));
  const [validation, setValidation] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  function field<K extends keyof OperationsPolicyInput>(key: K, value: OperationsPolicyInput[K]) {
    setInput((current) => ({ ...current, [key]: value }));
    setValidation('');
  }
  return (
    <dialog
      ref={dialog}
      className="modal operations-policy-modal"
      aria-labelledby="operations-policy-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="modal-header">
        <div>
          <h2 id="operations-policy-title">预算与运行策略</h2>
          <p>{companyName} · 保存按版本核验，适用于该企业后续模型请求。</p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="关闭预算策略"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const message = validatePolicyInput(input);
          if (message) setValidation(message);
          else void onSave(normalizePolicyRequest(input));
        }}
      >
        <div className="form-content operations-policy-content">
          <label className="operations-budget-toggle">
            <input
              type="checkbox"
              aria-label="开启企业月度模型额度控制"
              checked={input.quota_enabled}
              disabled={busy}
              onChange={(event) => field('quota_enabled', event.target.checked)}
            />
            <span>
              <strong>开启企业月度模型额度控制</strong>
              <small>新请求先预留费用；达到额度或有超额记录时，暂停新增模型处理。</small>
            </span>
          </label>
          <div className="operations-policy-fields">
            <label>
              <span className="field-label">月度额度（人民币元）</span>
              <input
                aria-label="企业月度模型额度"
                inputMode="decimal"
                value={input.monthly_limit}
                disabled={busy}
                onChange={(event) => field('monthly_limit', event.target.value)}
              />
            </label>
            <label>
              <span className="field-label">单次票据识别预留（元）</span>
              <input
                aria-label="单次票据识别费用预留"
                inputMode="decimal"
                value={input.ocr_reserve}
                disabled={busy}
                onChange={(event) => field('ocr_reserve', event.target.value)}
              />
            </label>
            <label>
              <span className="field-label">单次分录建议预留（元）</span>
              <input
                aria-label="单次分录建议费用预留"
                inputMode="decimal"
                value={input.proposal_reserve}
                disabled={busy}
                onChange={(event) => field('proposal_reserve', event.target.value)}
              />
            </label>
            <label>
              <span className="field-label">单次会计对话预留（元）</span>
              <input
                aria-label="单次会计对话费用预留"
                inputMode="decimal"
                value={input.chat_reserve ?? '0.00'}
                disabled={busy}
                onChange={(event) => field('chat_reserve', event.target.value)}
              />
            </label>
          </div>
          <p className="field-help">
            预留用于控制并发处理的费用风险，并非供应商报价或账单金额。开启控制时，额度与识别、分录两项预留都须大于零；会计对话预留为零时暂停新增对话。
          </p>
          {!pricingReady && (
            <p className="operations-price-warning">
              <TriangleAlert size={14} />
              模型单价尚未完整配置。开启额度控制后，仍需管理员配置报价才能发送模型请求。
            </p>
          )}
          <h3>站内提醒阈值</h3>
          <div className="operations-threshold-fields">
            {thresholds.map(({ key, label, unit, minimum, maximum }) => (
              <label key={key}>
                <span className="field-label">
                  {label}（{unit}）
                </span>
                <input
                  type="number"
                  min={minimum}
                  max={maximum}
                  step="1"
                  aria-label={label}
                  disabled={busy}
                  value={Number.isFinite(input[key]) ? String(input[key]) : ''}
                  onChange={(event) =>
                    field(key, event.target.value === '' ? Number.NaN : Number(event.target.value))
                  }
                />
              </label>
            ))}
          </div>
          {(validation || error) && (
            <p className="form-error" role="alert">
              <TriangleAlert size={15} />
              {validation || error}
            </p>
          )}
          <p className="field-help">
            提醒记录在工作台内，处置依据不会修改员工票据、会计凭证或外部账本。
          </p>
        </div>
        <div className="modal-footer">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="button primary" disabled={busy}>
            {busy ? <LoaderCircle className="spinner" size={15} /> : <Save size={15} />}保存预算策略
          </button>
        </div>
      </form>
    </dialog>
  );
}
