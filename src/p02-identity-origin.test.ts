import { describe, expect, it } from 'vitest';
import { validateIdentityOrigin } from '../scripts/p02_identity_origin';

describe('isolated browser identity origin', () => {
  const fixture = {
    auth_origin: 'https://c01-real-auth-25541.example.test',
    auth_local_origin: 'http://127.0.0.1:25541',
  };
  it('accepts only the exact loopback origin frozen by the gate', () => {
    expect(() => validateIdentityOrigin(fixture, 'http://127.0.0.1:25541')).not.toThrow();
    expect(() => validateIdentityOrigin(fixture, 'http://127.0.0.1:25542')).toThrow();
    expect(() => validateIdentityOrigin(fixture, undefined)).toThrow();
  });
  it.each([
    'https://production.invalid',
    'http://localhost:25541',
    'http://127.0.0.1:80',
    'http://127.0.0.1:25541/path',
    'http://user@127.0.0.1:25541',
    'http://127.0.0.1:25541?redirect=1',
    'http://127.0.0.1:65536',
  ])('rejects unsafe or non-origin target %s even if it is expected', (origin) => {
    expect(() =>
      validateIdentityOrigin({ ...fixture, auth_local_origin: origin }, origin),
    ).toThrow();
  });
  it('rejects production issuer and absent identities', () => {
    expect(() =>
      validateIdentityOrigin(
        { ...fixture, auth_origin: 'https://prod.invalid' },
        fixture.auth_local_origin,
      ),
    ).toThrow();
    expect(() =>
      validateIdentityOrigin(
        { ...fixture, auth_origin: 'https://c01-real-auth-25542.example.test' },
        fixture.auth_local_origin,
      ),
    ).toThrow();
    expect(() => validateIdentityOrigin(null, fixture.auth_local_origin)).toThrow();
  });
});
