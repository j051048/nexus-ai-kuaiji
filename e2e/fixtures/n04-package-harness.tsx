import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, useNavigate } from 'react-router-dom';
import { MonthlyPackagePanel } from '../../src/MonthlyPackagePanel';
import { setApiScope } from '../../src/api';
import type { Session } from '../../src/types';
import '../../src/tokens.css';
import '../../src/styles.css';

const session: Session = {
  token: 'synthetic-no-external-token',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'client',
  user_name: '虚构客户财务',
  role: 'client_member',
  scope_key: 'n04-local-only',
};
setApiScope(session);
function Harness() {
  const [book, setBook] = useState('book-a');
  const navigate = useNavigate();
  return (
    <main>
      <button onClick={() => setBook(book === 'book-a' ? 'book-b' : 'book-a')}>切换虚构企业</button>
      <button onClick={() => navigate('?package=22222222222222222222222222222222')}>
        打开第二版链接
      </button>
      <MonthlyPackagePanel session={session} bookId={book} clientOnly />
    </main>
  );
}
createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <Harness />
  </BrowserRouter>,
);
