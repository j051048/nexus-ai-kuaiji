export type ValueBaseline = {
  company_id: string;
  period: string;
  counts: {
    submitted_events: number;
    mapped_drafts: number;
    approved_drafts: number;
    returned_events: number;
  };
  rates: {
    draft_mapping_rate: number | null;
    first_pass_rate: number | null;
    exception_rate: number | null;
    review_conversion_rate: number | null;
  };
  elapsed: {
    system_elapsed_hours_median: number | null;
    system_elapsed_hours_samples: number;
    labour_time_verified: false;
    note: string;
  };
  cost: {
    ai_model_cost_micro: number;
    ai_cost_per_approved_micro: number | null;
    scope: 'ai_model_only';
    note: string;
  };
};

const integer = (value: unknown): boolean =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0;

export function readValueBaseline(value: unknown): ValueBaseline {
  if (!value || typeof value !== 'object') throw new Error('价值基线格式无效');
  const row = value as ValueBaseline;
  if (typeof row.company_id !== 'string' || !/^\d{4}-\d{2}$/.test(row.period)) {
    throw new Error('价值基线期间无效');
  }
  const counts = row.counts;
  if (
    !counts ||
    !['submitted_events', 'mapped_drafts', 'approved_drafts', 'returned_events'].every(
      (key) => integer(counts[key as keyof typeof counts]),
    )
  ) {
    throw new Error('价值基线计数无效');
  }
  const rates = row.rates;
  if (
    !rates ||
    !['draft_mapping_rate', 'first_pass_rate', 'exception_rate', 'review_conversion_rate'].every(
      (key) => {
        const item = rates[key as keyof typeof rates];
        return item === null || (typeof item === 'number' && item >= 0 && item <= 1);
      },
    )
  ) {
    throw new Error('价值基线比率无效');
  }
  if (row.elapsed?.labour_time_verified !== false || row.cost?.scope !== 'ai_model_only') {
    throw new Error('价值基线口径无效');
  }
  return row;
}

export function percent(value: number | null): string {
  return value === null ? '暂无' : `${(value * 100).toFixed(1)}%`;
}

export function cny(micro: number | null): string {
  if (micro === null) return '暂无';
  return `¥${(micro / 1_000_000).toFixed(2)}`;
}
