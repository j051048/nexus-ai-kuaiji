import type { ReviewSelection } from './review-types';

export interface ReviewWorkspaceSession {
  company: string;
  period: string;
  template: string;
  risk: string;
  offset: number;
  selected: string[];
  previewId: string | null;
  commandKey: string;
  packageItems: ReviewSelection[] | null;
}
export interface ReviewPackageSession {
  signature: string;
  packageId: string | null;
  selected: string[];
  prepareKey: string;
  confirmationKey: string;
  selectionKey: { signature: string; key: string };
  changed: boolean;
}
let currentScope: string | null = null;
let workspace: ReviewWorkspaceSession | null = null;
let packet: ReviewPackageSession | null = null;

/** Only navigation and command identifiers live in memory. No tokens, facts,
 * attachments or accounting lines are written to browser storage. */
export function clearReviewSession(): void {
  currentScope = null;
  workspace = null;
  packet = null;
}
function activate(scope: string): void {
  if (currentScope !== scope) {
    clearReviewSession();
    currentScope = scope;
  }
}
export function readReviewWorkspace(scope: string): ReviewWorkspaceSession | null {
  activate(scope);
  return workspace ? structuredClone(workspace) : null;
}
export function saveReviewWorkspace(scope: string, value: ReviewWorkspaceSession): void {
  activate(scope);
  workspace = structuredClone(value);
}
export function readReviewPackage(scope: string, signature: string): ReviewPackageSession | null {
  activate(scope);
  return packet?.signature === signature ? structuredClone(packet) : null;
}
export function saveReviewPackage(scope: string, value: ReviewPackageSession): void {
  activate(scope);
  packet = structuredClone(value);
}
export function clearReviewPackage(scope: string): void {
  activate(scope);
  packet = null;
}

export function inputOwnsKeyboard(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return !!target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]');
}
export function previewMediaType(mediaType: string): 'image' | 'pdf' | null {
  if (['image/png', 'image/jpeg', 'image/webp'].includes(mediaType)) return 'image';
  return mediaType === 'application/pdf' ? 'pdf' : null;
}
