import type { TaskKind } from './workspace-types';

export const taskLabels: Record<TaskKind, string> = {
  intake: '资料草稿',
  business: '我的事项',
  approval: '部门审批',
  finance: '财务核验',
  handoff: '移交会计',
  case: '异常与补件',
  review: '凭证复核',
  ocr: '票据识别',
  proposal: '草稿生成',
  export: '审核后导出',
  delivery: '交付核验',
  alert: '运行告警',
  migration: '旧账迁移',
  material: '周期资料',
};
