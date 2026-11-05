import { expect, it } from 'vitest';
import { packageLinkIdentity } from './monthly-package-read';
it('pins notification deep links to their exact version and manifest', () => {
  const id = 'a'.repeat(32),
    hash = 'b'.repeat(64);
  expect(packageLinkIdentity(`?package=${id}&package_version=2&manifest_sha256=${hash}`)).toEqual({
    id,
    version: 2,
    fingerprint: hash,
  });
  for (const query of [
    `?package=${id}&package_version=2`,
    `?package=${id}&package_version=0&manifest_sha256=${hash}`,
    `?package=${id}&package_version=2&package_version=3&manifest_sha256=${hash}`,
  ])
    expect(() => packageLinkIdentity(query)).toThrow();
  expect(packageLinkIdentity(`?package=${id}`)).toBeUndefined();
});
