/** Local acceptance only: bind the fixture to the gate's frozen identity port. */
export function validateIdentityOrigin(value: unknown, expectedOrigin: string | undefined): void {
  if (!value || typeof value !== 'object') throw new Error('Local identity fixture required');
  const fixture = value as Record<string, unknown>;
  if (
    typeof fixture.auth_origin !== 'string' ||
    typeof fixture.auth_local_origin !== 'string' ||
    fixture.auth_local_origin !== expectedOrigin ||
    !/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(expectedOrigin || '')
  )
    throw new Error('Isolated identity origin does not match frozen configuration');
  const port = Number(new URL(fixture.auth_local_origin).port);
  if (
    !Number.isInteger(port) ||
    port < 1024 ||
    port > 65535 ||
    fixture.auth_origin !== `https://c01-real-auth-${port}.example.test`
  ) {
    throw new Error('Unprivileged local identity port required');
  }
}
