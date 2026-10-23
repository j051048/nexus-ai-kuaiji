import { Children, isValidElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FirmCockpitContent } from './FirmCockpit';
import type { FirmSnapshot } from './firm-cockpit-focus';

const empty: FirmSnapshot = {
  period: '2025-12',
  summary: {
    clients: 0,
    opened: 0,
    closed: 0,
    overdue: 0,
    exceptions: 0,
    risk_clients: 0,
    projection_stale: 0,
  },
  stages: {},
  companies: [],
  workload: [],
  risks: [],
};

function button(node: ReactNode, label: string): (() => void) | undefined {
  for (const child of Children.toArray(node)) {
    if (!isValidElement<{ children?: ReactNode; onClick?: () => void }>(child)) continue;
    if (
      child.type === 'button' &&
      Children.toArray(child.props.children).some(
        (part) => typeof part === 'string' && part.trim() === label,
      )
    )
      return child.props.onClick;
    const nested = button(child.props.children, label);
    if (nested) return nested;
  }
}

describe('全所工作台跳转保留当前服务月份', () => {
  it.each(['客户进度', '新增客户'])('%s 回调传递当前快照的月份', (label) => {
    const onMonthly = vi.fn(),
      onOnboarding = vi.fn();
    const tree = FirmCockpitContent({
      data: empty,
      onMonthly,
      onOnboarding,
      onOpenBook: vi.fn(),
      onOpenInbox: vi.fn(),
    });
    const action = button(tree, label);
    expect(action).toBeTypeOf('function');
    action!();
    expect(label === '客户进度' ? onMonthly : onOnboarding).toHaveBeenCalledWith('2025-12');
  });
});
