import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DiffList, WorkflowDiagram } from './WorkflowStudioPanel';
import {
  centsFromYuanInput,
  diffLines,
  formatCents,
  layoutWorkflow,
  rollbackBlockReason,
  routingIssues,
  yuanInputFromCents,
  type WorkflowDiff,
  type WorkflowInstance,
  type WorkflowNode,
} from './workflow-studio';

const nodes: WorkflowNode[] = [
  {
    id: 'draft',
    label: '发起报销',
    role: 'creator',
    start: true,
    edges: [
      { to: 'finance_review', condition: { amount_cents_lt: 500_000 } },
      { to: 'manager_review' },
    ],
  },
  {
    id: 'manager_review',
    label: '主管审批',
    role: 'manager',
    approval: true,
    edges: [{ to: 'finance_review' }],
  },
  {
    id: 'finance_review',
    label: '财务复核',
    role: 'reviewer',
    approval: true,
    terminal: true,
    irreversible: true,
    edges: [],
  },
];

const instance = (overrides: Partial<WorkflowInstance> = {}): WorkflowInstance => ({
  id: 'instance-1',
  company_id: 'company-1',
  book_id: 'book-1',
  definition_id: 'definition-1',
  definition_version: 1,
  process_kind: 'expense_reimbursement',
  process_label: '费用报销',
  amount_cents: 100_000,
  current_node: 'manager_review',
  path: ['draft', 'manager_review', 'finance_review'],
  status: 'running',
  suspended_reason: '',
  terminal_external_locked: false,
  irreversible_reached: false,
  created_by: 'user-1',
  created_at: '2026-11-01T00:00:00+00:00',
  updated_at: '2026-11-01T00:00:00+00:00',
  ...overrides,
});

describe('workflow money helpers stay on integer cents', () => {
  it('formats and parses cents without floating point drift', () => {
    expect(formatCents(10_000_000)).toBe('100,000.00');
    expect(formatCents(9_965_000)).toBe('99,650.00');
    expect(formatCents(1)).toBe('0.01');
    expect(formatCents(0)).toBe('0.00');
    expect(centsFromYuanInput('5000')).toBe(500_000);
    expect(centsFromYuanInput('5000.5')).toBe(500_050);
    expect(centsFromYuanInput('5000.55')).toBe(500_055);
    expect(yuanInputFromCents(500_000)).toBe('5000');
    expect(yuanInputFromCents(500_055)).toBe('5000.55');
  });

  it('rejects malformed or negative thresholds instead of guessing', () => {
    expect(centsFromYuanInput('1.234')).toBeNull();
    expect(centsFromYuanInput('-5')).toBeNull();
    expect(centsFromYuanInput('')).toBeNull();
    expect(centsFromYuanInput('五千')).toBeNull();
    expect(
      routingIssues({
        auto_approve_below_cents: 600_000,
        dual_approval_from_cents: 500_000,
        escalate_after_hours: 24,
      }),
    ).toEqual(['低风险直通门槛不能高于双人审批门槛']);
    expect(
      routingIssues({
        auto_approve_below_cents: 0,
        dual_approval_from_cents: 0,
        escalate_after_hours: 0,
      }),
    ).toEqual(['超时升级时限必须在 1 到 720 小时之间']);
  });
});

describe('irreversible instances refuse rollback with a reason', () => {
  it('names the write-off route instead of silently allowing a revert', () => {
    expect(rollbackBlockReason(instance())).toBeNull();
    expect(rollbackBlockReason(instance({ terminal_external_locked: true }))).toContain('冲销');
    expect(rollbackBlockReason(instance({ irreversible_reached: true }))).toContain('冲销');
    expect(rollbackBlockReason(instance({ status: 'completed' }))).toContain('已结束');
  });
});

describe('workflow layout', () => {
  it('places every node and routes each conditional edge to the right column', () => {
    const layout = layoutWorkflow(nodes);
    expect(layout.boxes).toHaveLength(3);
    expect(layout.links).toHaveLength(3);
    expect(layout.startId).toBe('draft');
    const byId = new Map(layout.boxes.map((box) => [box.id, box]));
    expect(byId.get('finance_review')!.x).toBeGreaterThan(byId.get('draft')!.x);
    expect(byId.get('manager_review')!.y).toBeGreaterThanOrEqual(byId.get('draft')!.y);
    expect(layout.width).toBeGreaterThan(320);
    for (const link of layout.links) expect(link.path).not.toContain('NaN');
    const conditional = layout.links.find((link) => !link.fallback);
    expect(conditional?.label).toBe('金额 < 5,000.00 元');
    expect(layout.links.filter((link) => link.fallback)).toHaveLength(1);
  });

  it('never invents a start node when the definition is empty', () => {
    const layout = layoutWorkflow([]);
    expect(layout.boxes).toHaveLength(0);
    expect(layout.startId).toBe('');
    expect(layout.width).toBeGreaterThan(0);
  });
});

describe('version diff wording', () => {
  const diff: WorkflowDiff = {
    added_nodes: [],
    removed_nodes: [],
    changed_nodes: [
      { id: 'manager_review', fields: { role: { before: 'manager', after: 'reviewer' } } },
    ],
    routing: { dual_approval_from_cents: { before: 500_000, after: 300_000 } },
    start_changed: false,
    identical: false,
    from: { id: 'a', version: 1, status: 'archived', published_at: null },
    to: { id: 'b', version: 2, status: 'active', published_at: '2026-11-02T00:00:00+00:00' },
  };

  it('reports thresholds in yuan and names changed nodes', () => {
    const lines = diffLines(diff, nodes);
    expect(lines).toContain('双人审批门槛：5,000.00 元 → 3,000.00 元');
    expect(lines).toContain('调整节点：主管审批（role）');
  });

  it('says plainly when two versions are identical', () => {
    expect(diffLines({ ...diff, changed_nodes: [], routing: {} })).toEqual([
      '两个版本规则与指纹一致，没有差异',
    ]);
  });
});

describe('workflow diagram rendering', () => {
  const html = renderToStaticMarkup(
    createElement(WorkflowDiagram, { nodes, ariaLabel: '费用报销流程图' }),
  );

  it('renders native SVG with node roles, status marks and edge conditions', () => {
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="费用报销流程图"');
    expect(html).toContain('<svg');
    expect(html).toContain('发起报销');
    expect(html).toContain('主管 · 需审批');
    expect(html).toContain('复核人 · 需审批 · 结束 · 不可逆');
    expect(html).toContain('起点');
    expect(html).toContain('金额 &lt; 5,000.00 元');
    expect(html).toContain('workflow-edge fallback');
    expect(html).not.toContain('window.confirm');
  });

  it('renders the diff list with both version numbers', () => {
    const diffHtml = renderToStaticMarkup(
      createElement(DiffList, {
        diff: {
          added_nodes: ['audit_note'],
          removed_nodes: [],
          changed_nodes: [],
          routing: {},
          start_changed: false,
          identical: false,
          from: { id: 'a', version: 1, status: 'archived', published_at: null },
          to: { id: 'b', version: 2, status: 'active', published_at: null },
        },
        nodes,
      }),
    );
    expect(diffHtml).toContain('版本 1');
    expect(diffHtml).toContain('版本 2');
    expect(diffHtml).toContain('新增节点：audit_note');
  });
});
