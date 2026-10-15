import { useCallback } from 'react';
import { createRoot } from 'react-dom/client';
import { useAccountingAuth } from '../../src/useAccountingAuth';

/** Exercise the real hook while an identity request is in flight. The product's
 * navigation normally disables switching until that request has completed. */
function AuthScopeHarness() {
  const clearWorkspace = useCallback(() => {}, []);
  const auth = useAccountingAuth(clearWorkspace);
  return (
    <main>
      <output data-testid="identity">{auth.session?.user_name ?? '未登录'}</output>
      <output data-testid="seed">{auth.homeBootstrap ? '已有首页数据' : '没有首页数据'}</output>
      <button onClick={() => void auth.logout()}>退出</button>
      <button disabled={auth.loading} onClick={() => void auth.choosePersona('employee')}>
        使用员工身份
      </button>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<AuthScopeHarness />);
