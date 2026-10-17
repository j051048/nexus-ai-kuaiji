import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { AppErrorBoundary, AppRouteError } from './AppErrorBoundary';
import { cleanupRecoveryParam, installPreloadRecovery } from './preload-recovery';
import './styles.css';
import './agent-shell.css';

installPreloadRecovery();

const router = createBrowserRouter([
  { path: '*', element: <App />, errorElement: <AppRouteError /> },
]);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <RouterProvider router={router} />
    </AppErrorBoundary>
  </React.StrictMode>,
);

cleanupRecoveryParam();
