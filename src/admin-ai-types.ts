import type { CompanyStatus } from './admin-types';
import type { OperationsOverview } from './operations-types';

export interface AdminAISettings {
  enabled: boolean;
  updated_at: string | null;
  can_manage: boolean;
  provider_label: string;
  ocr_ready: boolean;
  journal_ready: boolean;
  chat_ready: boolean;
  configured: boolean;
}

export interface AdminAICompany {
  id: string;
  name: string;
  status: CompanyStatus;
  version: number;
}

export type AdminAIOverview =
  | (OperationsOverview & {
      active: true;
      company: AdminAICompany;
      ai: AdminAISettings;
      usage: OperationsOverview['usage'] & { total_tokens?: number | null };
    })
  | {
      active: false;
      company: AdminAICompany;
      ai: AdminAISettings;
      period?: string;
      currency?: 'CNY';
    };
