import type { BusinessAttachment, BusinessPayload, BusinessTemplate } from './business-types';
import type { OcrResult } from './intelligence-types';

export interface IntakeRun {
  id: string; attachment_id: string; status: 'running' | 'succeeded' | 'failed' | 'stale';
  result: OcrResult | null; error_code: string; error_detail: string;
  checks: { code: string; severity: string; payload: { message: string; field?: string; document_index?: number } }[];
}
export interface IntakeDraft {
  id: string; company_id: string; company_name: string; department_id: string; department_name: string;
  template_id: BusinessTemplate; fields: BusinessPayload; status: 'draft' | 'completed'; version: number;
  event_id: string | null; attachments: BusinessAttachment[]; runs: IntakeRun[]; updated_at: string;
  vision_ready: boolean; ai_enabled: boolean;
}
