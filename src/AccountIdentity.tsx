import { useEffect, useState } from 'react';
import { ChevronDown, UserRound } from 'lucide-react';
import { accountIdentity, type AccountIdentityOptions } from './account-identity';
import './account-identity.css';

export function AccountIdentity(props: AccountIdentityOptions) {
  const { session, profile, me } = props;
  const [denied, setDenied] = useState<{
    scope: string;
    profile: typeof profile;
    me: typeof me;
  } | null>(null);
  useEffect(() => {
    const onForbidden = (event: Event) => {
      if ((event as CustomEvent<string>).detail === session.scope_key)
        setDenied({ scope: session.scope_key, profile, me });
    };
    window.addEventListener('accounting:forbidden', onForbidden);
    return () => window.removeEventListener('accounting:forbidden', onForbidden);
  }, [session.scope_key, profile, me]);
  // A resource denial is not logout. Stop making capability claims until the
  // parent publishes fresh identity/profile data; do not fetch or retry here.
  const unavailable =
    props.unavailable ||
    (denied?.scope === session.scope_key && denied.profile === profile && denied.me === me);
  const identity = accountIdentity({ ...props, unavailable });
  return (
    <section className="account-identity" aria-label="当前登录身份">
      <details key={`${session.scope_key}:${identity.role}:${identity.confirmed}`}>
        <summary>
          <UserRound size={17} aria-hidden="true" />
          <span className="account-identity-unit">
            <span className="account-identity-prefix">{identity.unitLabel}</span>
            <strong className="account-identity-unit-name" title={identity.unitName}>
              {identity.unitName}
            </strong>
          </span>
          <strong className="account-identity-role">{identity.role}</strong>
          <span className="account-identity-name">{identity.name}</span>
          <span className="account-identity-disclosure">
            权限与范围 <ChevronDown size={15} aria-hidden="true" />
          </span>
        </summary>
        <div className="account-identity-details">
          <dl>
            <div>
              <dt>{identity.clientIdentity ? '服务事务所' : '所属事务所'}</dt>
              <dd>{identity.confirmed ? identity.firm : '待确认'}</dd>
            </div>
            {identity.companyMemberships.map((company) => (
              <div key={company.id}>
                <dt>企业成员身份</dt>
                <dd>
                  {company.name} · {company.role}
                </dd>
              </div>
            ))}
            <div>
              <dt>授权范围</dt>
              <dd>{identity.scope}</dd>
            </div>
          </dl>
          {identity.confirmed ? (
            <>
              {identity.capabilities.length > 0 && (
                <p>当前具备：{identity.capabilities.join('、')}。</p>
              )}
              <p>
                各项能力仅适用于获授权的具体企业、部门或账套，以办理时的权限核验为准。凭证仍须会计复核后导出。
              </p>
            </>
          ) : (
            <p>当前权限尚未确认，请重新读取权限或联系管理员。此处不会自动重试业务操作。</p>
          )}
        </div>
      </details>
    </section>
  );
}
