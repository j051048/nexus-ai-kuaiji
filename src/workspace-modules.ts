import { lazy } from 'react';

export const NewBookModal = lazy(() =>
  import('./AppDialogs').then((module) => ({ default: module.NewBookModal })),
);
export const ClientAccessTools = lazy(() =>
  import('./ClientAccessTools').then((module) => ({ default: module.ClientAccessTools })),
);

export const AccessManagement = lazy(() =>
  import('./AccessManagement').then((module) => ({ default: module.AccessManagement })),
);
export const AdminWorkspace = lazy(() => import('./AdminWorkspace'));
export const AdminMfaGate = lazy(() => import('./AdminMfaGate'));
export const AccountActivation = lazy(() => import('./AccountActivation'));
export const AccountRecovery = lazy(() => import('./AccountRecovery'));
export const ChineseImportWizard = lazy(() => import('./ChineseImportWizard'));
export const InlineReviewDrawer = lazy(() =>
  import('./InlineReviewDrawer').then((module) => ({ default: module.InlineReviewDrawer })),
);
export const ProposalTaskDialog = lazy(() =>
  import('./ProposalTaskDialog').then((module) => ({ default: module.ProposalTaskDialog })),
);
export const GlobalAccountingAssistant = lazy(() =>
  import('./GlobalAccountingAssistant').then((module) => ({
    default: module.GlobalAccountingAssistant,
  })),
);
export const AccountingContextAssistant = lazy(() =>
  import('./AccountingContextAssistant').then((module) => ({
    default: module.AccountingContextAssistant,
  })),
);
