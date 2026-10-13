import type { Pagination } from './types';

export type AssistantTaskKind = 'ocr_batch' | 'proposal_batch' | 'bank_drafts';
export type AssistantTurnStatus =
  'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted' | 'stale' | 'cancelled';
export interface AssistantInitialContext {
  companyId?: string;
  bookId?: string;
  draftId?: string;
  eventId?: string;
}
export interface AssistantBookOption {
  id: string;
  name: string;
  period: string;
  period_status?: string;
}
export interface AssistantEventOption {
  id: string;
  title?: string;
  description?: string;
  amount?: string;
  status: string;
  version?: number;
}
export interface AssistantScopeOptions {
  company_id: string;
  company_name: string;
  books: AssistantBookOption[];
  events: AssistantEventOption[];
  pagination?: Pagination;
  model_available?: boolean;
  notice?: string;
}
export interface AssistantConversation {
  id: string;
  company_id: string;
  title: string;
  book_ids: string[];
  event_ids: string[];
  draft_id?: string | null;
  scope_version: number;
  created_at: string;
  updated_at: string;
}
export interface AssistantConversationPage {
  conversations: AssistantConversation[];
  pagination: Pagination;
}
export interface AssistantReply {
  answer: string;
  citation_ids: string[];
  proposals: { kind: AssistantTaskKind; reason: string }[];
}
export interface AssistantTurn {
  id: string;
  question: string;
  status: AssistantTurnStatus;
  mode: 'model' | null;
  error_code?: string | null;
  error_detail?: string | null;
  reply: AssistantReply | null;
  created_at?: string;
  updated_at?: string;
  input_fingerprint?: string;
}
export interface AssistantEvidence {
  id: string;
  kind: string;
  label: string;
  detail: string;
  book_id?: string;
  draft_id?: string;
  event_id?: string;
  attachment_id?: string;
  source_id?: string;
  [key: string]: unknown;
}
export interface AssistantContext {
  company_id: string;
  company_name: string;
  books: AssistantBookOption[];
  events: AssistantEventOption[];
  draft?: { id: string; version?: number; description?: string } | null;
  evidence: AssistantEvidence[];
  input_fingerprint: string;
  model_available?: boolean;
  notice?: string;
}
export interface AssistantConversationDetail {
  conversation: AssistantConversation;
  turns: AssistantTurn[];
  context: AssistantContext;
  model_available?: boolean;
  notice?: string;
}
export interface AssistantMessage {
  id: string;
  turn_id: string;
  role: 'user' | 'assistant';
  content: { question: string } | AssistantReply;
  created_at: string;
  input_fingerprint?: string;
  citations?: AssistantEvidence[];
}
export interface AssistantMessages {
  messages: AssistantMessage[];
  pagination: Pagination;
}
export interface AssistantActionReceipt {
  id: string;
  plan_id: string;
  kind: AssistantTaskKind;
  count: number;
  batch_id?: string | null;
  jobs: { id: string; event_id: string; draft_id?: string; status: string }[];
  draft_refs?: { id: string; book_id: string }[];
  draft_ids: string[];
  created: number;
  exceptions: number;
  created_at: string;
  progress?: {
    total?: number;
    completed?: number;
    succeeded?: number;
    failed?: number;
    queued?: number;
    running?: number;
    [key: string]: unknown;
  };
  status?: string;
}
export interface AssistantActionPlan {
  id: string;
  kind: AssistantTaskKind;
  state: 'ready' | 'empty' | 'executed' | 'expired';
  fingerprint: string;
  expires_at: string;
  scope: { company_id: string; book_ids: string[]; event_ids: string[]; draft_id?: string | null };
  count: number;
  items: {
    title: string;
    event_id?: string;
    attachment_id?: string;
    book_id?: string;
    draft_id?: string;
    source_id?: string;
  }[];
  blocked: { code: string; message: string }[];
  reason: string;
  requires_confirmation: true;
  receipt?: AssistantActionReceipt | null;
}
export interface AssistantActionPage {
  plans: AssistantActionPlan[];
  pagination: Pagination;
}
