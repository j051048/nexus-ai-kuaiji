import type { NavigationCapabilities } from './workspace-navigation';
import type { WorkspaceProfile } from './workspace-types';

/** Resolved app capabilities include its existing legacy-profile fallbacks. */
export interface NavigationContext {
  accounting: boolean;
  processing: boolean;
  operations: boolean;
  hasBook: boolean;
  manageAdmin: boolean;
  manageFirm: boolean;
}

/** Display projection only: server authorization remains the authority for every action. */
export function projectNavigationCapabilities(
  profile: WorkspaceProfile | null,
  context: NavigationContext,
): NavigationCapabilities {
  return {
    accounting: context.accounting,
    monthly: context.accounting || !!profile?.can_manage,
    review: !!profile?.can_review,
    operations: context.operations,
    quality: !!profile?.can_review && context.hasBook,
    processing: context.processing,
    business: !!(
      profile &&
      (profile.can_submit ||
        profile.can_approve ||
        profile.can_finance ||
        profile.can_accounting ||
        profile.can_manage)
    ),
    admin: context.manageAdmin || context.manageFirm,
    access: context.manageFirm,
  };
}
