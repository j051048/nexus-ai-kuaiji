import type { BusinessEvent } from './business-types';
import type { Pagination } from './types';
import { uploadSizeError } from './transport-limits';

export interface ServicePlanCandidate {
  user_id: string;
  name: string;
  can_lead: boolean;
  can_review: boolean;
  can_contact: boolean;
}
export interface ServicePlan {
  book_id: string;
  version: number;
  lead_user_id: string | null;
  reviewer_user_id: string | null;
  backup_user_id: string | null;
  contact_user_id: string | null;
  due_on: string;
  notes: string;
  updated_at: string;
  updated_by: string;
}
export type MaterialMode = 'provided' | 'no_activity' | 'not_applicable';
export interface MaterialAttachment {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_by: string;
  created_at: string;
}
export interface MaterialRequest {
  id: string;
  book_id: string;
  company_id: string;
  company_name: string;
  period: string;
  title: string;
  description: string;
  required: boolean;
  due_on: string;
  assignee_user_id: string | null;
  assignee_name: string | null;
  state: 'pending' | 'submitted' | 'returned' | 'accepted';
  version: number;
  attachments?: MaterialAttachment[];
  latest_submission: null | {
    id: string;
    mode: MaterialMode;
    note: string;
    event_ids: string[];
    attachments?: MaterialAttachment[];
    evidence: {
      event_id: string;
      event_version: number;
      title: string;
      attachments: { id: string; filename: string; sha256: string }[];
    }[];
    submitted_by: string;
    submitted_at: string;
  };
  last_review: null | {
    action: 'accept' | 'return';
    reason: string;
    reviewed_by: string;
    reviewed_at: string;
  };
  permissions: { submit: boolean; review: boolean };
  evidence_current: boolean | null;
}
export interface MaterialTemplate {
  id: string;
  name: string;
  version: number;
  items: MaterialTemplateItem[];
}
export interface MaterialTemplateItem {
  key: string;
  title: string;
  description: string;
  required: boolean;
  due_day: number;
  due_month_offset?: 0 | 1;
  assignee_user_id: string | null;
}
export interface ServicePlanOverview {
  book: {
    id: string;
    company_id: string;
    company_name: string;
    name: string;
    period: string;
    ledger_id: string;
    period_status?: 'open' | 'closed';
  };
  permissions: { manage: boolean; review: boolean };
  plan: ServicePlan | null;
  candidates: ServicePlanCandidate[];
  requirements: MaterialRequest[];
  requirements_pagination: Pagination;
  templates: MaterialTemplate[];
}
export interface MaterialRequests {
  rows: MaterialRequest[];
  pagination: Pagination;
}
export const materialStateLabels: Record<MaterialRequest['state'], string> = {
  pending: '待提供',
  submitted: '待独立验收',
  returned: '已退回补充',
  accepted: '资料已验收',
};
export const materialModeLabels: Record<MaterialMode, string> = {
  provided: '提供资料',
  no_activity: '本月无发生',
  not_applicable: '本企业不适用',
};

/** A client-side hint only; the server rechecks membership, period and evidence versions. */
export function eligibleMaterialEvent(
  event: BusinessEvent,
  request: Pick<MaterialRequest, 'company_id' | 'period'>,
  userId: string,
): boolean {
  return (
    event.company_id === request.company_id &&
    event.applicant_id === userId &&
    event.payload.occurred_on.slice(0, 7) === request.period &&
    event.attachments.some((item) => !item.superseded) &&
    event.status !== 'draft'
  );
}
export function materialSubmissionIssue(
  mode: MaterialMode,
  note: string,
  eventIds: string[],
  attachmentIds: string[] = [],
) {
  if (note.trim().length < 3) return '请填写至少 3 个字的说明，供验收人核对。';
  if (note.trim().length > 1000) return '说明最多 1000 个字。';
  if (mode === 'provided' && !eventIds.length && !attachmentIds.length)
    return '请上传原件，或选择本企业、本月的一项带附件业务资料。';
  if (mode === 'provided' && (eventIds.length > 30 || new Set(eventIds).size !== eventIds.length))
    return '每次最多关联 30 项资料，且不能重复选择同一事项。';
  return '';
}
export function materialAttachmentIssue(
  file: Pick<File, 'name' | 'size'>,
  attachments: Pick<MaterialAttachment, 'size_bytes'>[],
) {
  if (!/\.(pdf|png|jpe?g|webp|xml|csv|xlsx)$/i.test(file.name))
    return '资料支持 PDF、图片、XML、CSV 和 XLSX；请先另存为支持的格式。';
  const sizeIssue = uploadSizeError(file.size);
  if (sizeIssue) return sizeIssue;
  if (attachments.length >= 10) return '每项资料最多保存 10 个原件。';
  if (attachments.reduce((sum, item) => sum + item.size_bytes, 0) + file.size > 50 * 1024 * 1024)
    return '每项资料的原件总大小不能超过 50 MB。';
  return '';
}
export function servicePlanAssignmentIssue(
  leadUserId: string,
  reviewerUserId: string,
  candidates: ServicePlanCandidate[],
) {
  if (leadUserId && reviewerUserId && leadUserId === reviewerUserId)
    return '主办会计与独立复核人需由不同人员担任。';
  if (leadUserId && !candidates.some((item) => item.user_id === leadUserId && item.can_lead))
    return '主办会计的当前账套权限已变更，请重新选择。';
  if (
    reviewerUserId &&
    !candidates.some((item) => item.user_id === reviewerUserId && item.can_review)
  )
    return '独立复核人的当前账套权限已变更，请重新选择。';
  return '';
}
