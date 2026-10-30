import React from 'react';
import { createRoot } from 'react-dom/client';
import { FormalClosePanel } from '../../src/FormalClosePanel';
import { setApiScope } from '../../src/api';
import type { Session } from '../../src/types';
import '../../src/tokens.css';
import '../../src/styles.css';
const session: Session = {
  token: 'synthetic-boundary-test-only',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'reviewer',
  user_name: '虚构复核员',
  role: 'reviewer',
  scope_key: 'p02-boundary-only',
};
setApiScope(session);
createRoot(document.getElementById('root')!).render(
  <FormalClosePanel session={session} bookId="book-a" period="2026-09" canReview />,
);
