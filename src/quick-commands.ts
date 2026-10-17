import type { AgentCommand } from './AgentCommandPalette';
import type { AgentNavigationView } from './AgentNavigation';

export function buildQuickCommands({
  navigate,
  openAssistant,
  canSeeAccounting,
  canReview,
  processing,
  businessLabel,
}: {
  navigate: (view: AgentNavigationView) => void;
  openAssistant: () => void;
  canSeeAccounting: boolean;
  canReview: boolean;
  processing: boolean;
  businessLabel: string;
}): AgentCommand[] {
  return [
    {
      id: 'assistant',
      label: '会计助手',
      description: '按企业与账期提问、预览后台任务',
      run: openAssistant,
    },
    {
      id: 'home',
      label: '我的待办',
      description: '集中处理授权范围内的下一步工作',
      run: () => navigate('home'),
    },
    {
      id: 'business',
      label: businessLabel,
      description: '录入、补件与审批进度',
      run: () => navigate('business'),
    },
    {
      id: 'materials',
      label: '资料清单',
      description: '本月待交、待补充及待验收资料',
      run: () => navigate('materials'),
    },
    {
      id: 'runs',
      label: '后台运行',
      description: '查看真实识别、分录与迁移任务',
      run: () => navigate('runs'),
    },
    {
      id: 'cases',
      label: '异常与补件',
      description: '核对问题并联系资料提供人',
      run: () => navigate('cases'),
    },
    ...(canSeeAccounting
      ? [
          {
            id: 'monthly',
            label: '客户月度进度',
            description: '按企业与账期定位复核、异常及交付',
            run: () => navigate('monthly'),
          },
          {
            id: 'companies',
            label: '客户企业',
            description: '进入已授权账套与交付工作区',
            run: () => navigate('accounting'),
          },
        ]
      : []),
    ...(canReview
      ? [
          {
            id: 'review',
            label: '跨企业批量复核',
            description: '准备可比较的凭证复核包',
            run: () => navigate('review'),
          },
        ]
      : []),
  ].filter((command) => command.id !== 'runs' || processing);
}
