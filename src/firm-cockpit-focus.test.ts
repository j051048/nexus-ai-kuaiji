import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FirmCockpitContent } from './FirmCockpit';
import {
  coordinationReasons,
  dueLabel,
  selectCoordinationClients,
  type FirmClient,
  type FirmSnapshot,
} from './firm-cockpit-focus';

const client = (value: Partial<FirmClient> = {}): FirmClient => ({
  company_id: 'company-a',
  company_name: '虚构客户甲',
  book_id: 'book-a',
  period: '2026-10',
  period_status: 'open',
  stage: 'accounting',
  tasks: { intake: 0, review: 0, exception: 0, delivery: 0, overdue: 0 },
  lead_user_id: 'accountant-a',
  lead_name: '林会计',
  reviewer_name: '王复核',
  due_on: '2026-10-28',
  risk: 0,
  ...value,
});

const snapshot = (value: Partial<FirmSnapshot> = {}): FirmSnapshot => ({
  period: '2026-10',
  summary: {
    clients: 35,
    opened: 30,
    closed: 10,
    overdue: 0,
    exceptions: 0,
    risk_clients: 0,
    projection_stale: 2,
  },
  stages: { accounting: 2, closed: 10 },
  companies: [client()],
  workload: [],
  risks: [],
  ...value,
});

const content = (data: FirmSnapshot, extra = {}) =>
  renderToStaticMarkup(
    createElement(FirmCockpitContent, {
      data,
      onOpenBook: () => undefined,
      onOpenInbox: () => undefined,
      ...extra,
    }),
  );

describe('事务所管理工作台聚焦实际业务', () => {
  it('管理者先看到简短全所进度，再处理需协调客户', () => {
    const html = content(snapshot());
    expect(html.indexOf('全所运营概览')).toBeLessThan(html.indexOf('需要你协调'));
  });
  it('不会把风险分、复核待办或投影陈旧本身解释为客户业务异常', () => {
    expect(coordinationReasons(client({ risk: 99 }))).toEqual([]);
    expect(
      selectCoordinationClients([
        client({
          risk: 99,
          tasks: { intake: 0, review: 3, exception: 0, delivery: 0, overdue: 0 },
        }),
      ]),
    ).toEqual([]);
  });

  it('明确列出需协调原因，优先逾期与实际异常，负责人不足明确待安排', () => {
    const unassigned = client({ company_id: 'unassigned', lead_user_id: null, lead_name: '' });
    const overdue = client({
      company_id: 'overdue',
      tasks: { intake: 0, review: 0, exception: 2, delivery: 0, overdue: 1 },
    });
    expect(coordinationReasons(unassigned)).toEqual(['主办会计待安排']);
    expect(coordinationReasons(overdue)).toEqual(['1 项待办已逾期', '2 项异常待处理']);
    expect(selectCoordinationClients([unassigned, overdue]).map((row) => row.company_id)).toEqual([
      'overdue',
      'unassigned',
    ]);
  });

  it('截止日期不猜测，拒绝无效日期', () => {
    expect(dueLabel('')).toBe('期限待确认');
    expect(dueLabel('2026-02-31')).toBe('期限待确认');
    expect(dueLabel('2026-10-28')).toBe('10月28日');
  });

  it('首屏呈现本月安排、需协调客户和人员，不暴露原始诊断口径', () => {
    const html = content(snapshot());
    const visible = html.split('<details')[0];
    expect(visible).toContain('本月服务进度');
    expect(visible).toContain('需要你协调');
    expect(visible).toContain('人员安排');
    expect(visible).toContain('35');
    expect(visible).toContain('已完成月结');
    expect(visible).not.toContain('风险分');
    expect(visible).not.toContain('投影');
    expect(visible.replace(/<[^>]*>/g, '')).not.toContain('open');
    expect(html).toContain('<details');
  });

  it('显示真实主办与截止日期，不编造缺省负责人或期限', () => {
    const html = content(
      snapshot({ companies: [client({ lead_user_id: null, lead_name: '', due_on: '' })] }),
    );
    expect(html).toContain('主办会计待安排');
    expect(html).toContain('期限待确认');
    expect(html).not.toContain('林会计');
  });

  it('企业待办可能跨月，不冒充当月任务或会计个人指派量', () => {
    const html = content(
      snapshot({
        companies: [
          client({ tasks: { intake: 0, review: 0, exception: 1, delivery: 0, overdue: 0 } }),
        ],
        workload: [
          {
            user_id: 'accountant-a',
            name: '林会计',
            clients: 1,
            pending: 7,
            exceptions: 1,
            overdue: 0,
            risk_clients: 1,
          },
        ],
      }),
    );
    const visible = html.split('<details')[0].replace(/<[^>]*>/g, '');
    expect(visible).toContain('企业当前未结事项，可能跨月');
    expect(visible).toContain('客户合计待办');
    expect(visible).toContain('不是个人指派量');
    expect(visible).toContain('2026-10 服务单');
    expect(html).toContain('负责人和期限取自所选月份的服务单');
    expect(html).toContain('待办未按月份拆分');
    expect(visible).not.toContain('本月任务 7');
  });

  it('无客户时给出开通行动，未提供回调时不显示虚假可用入口', () => {
    const data = snapshot({
      companies: [],
      summary: {
        clients: 0,
        opened: 0,
        closed: 0,
        overdue: 0,
        exceptions: 0,
        risk_clients: 0,
        projection_stale: 0,
      },
    });
    expect(content(data)).toContain('从第一家客户开始');
    expect(content(data)).not.toContain('新增客户</button>');
    expect(content(data, { onOnboarding: () => undefined })).toContain('新增客户');
  });

  it('全部锁期也不声称交付完成，兼任入口由已有授权回调控制', () => {
    const html = content(
      snapshot({
        summary: {
          clients: 1,
          opened: 1,
          closed: 1,
          overdue: 0,
          exceptions: 0,
          risk_clients: 0,
          projection_stale: 0,
        },
      }),
      { onMonthly: () => undefined, onTeam: () => undefined },
    );
    expect(html).toContain('客户进度');
    expect(html).toContain('人员管理');
    expect(html).toContain('交付结果请在客户进度中确认');
    expect(html).not.toContain('全部交付完成');
  });
});
