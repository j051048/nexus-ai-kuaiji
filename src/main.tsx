import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { AppErrorBoundary, AppRouteError } from './AppErrorBoundary';
import { cleanupRecoveryParam, installPreloadRecovery } from './preload-recovery';
import { registerPwaRuntime } from './pwa-install-trigger';
const ConfirmHost = lazy(() =>
  import('./confirm-dialog').then((module) => ({ default: module.ConfirmHost })),
);
const PwaInstallHost = lazy(() =>
  import('./PwaInstallHost').then((module) => ({ default: module.PwaInstallHost })),
);
import './tokens.css';
import './styles.css';
import './agent-shell.css';

installPreloadRecovery();
registerPwaRuntime();

const router = createBrowserRouter([
  { path: '*', element: <App />, errorElement: <AppRouteError /> },
]);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <RouterProvider router={router} />
      <Suspense fallback={null}>
        <ConfirmHost />
        <PwaInstallHost />
      </Suspense>
    </AppErrorBoundary>
  </React.StrictMode>,
);

cleanupRecoveryParam();
