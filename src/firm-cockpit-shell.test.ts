import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { FirmCockpit } from './FirmCockpit';
import type { Session } from './types';

it('负责人从业务工作台进入，初始加载也给出明确主任务', () => {
  const html = renderToStaticMarkup(
    createElement(FirmCockpit, {
      session: {
        mode: 'local',
        token: 'offline',
        firm_id: 'firm-a',
        user_id: 'manager-a',
        user_name: '测试负责人',
        firm_name: '虚构事务所',
        role: 'admin',
        scope_key: 'firm-a',
      } as Session,
      onOpenBook: () => undefined,
      onOpenInbox: () => undefined,
    }),
  );
  expect(html).toContain('全所工作台');
  expect(html).toContain('客户进度、待协调事项和人员安排');
  expect(html).not.toContain('FIRM OPERATIONS / ONE TRUTH');
});
