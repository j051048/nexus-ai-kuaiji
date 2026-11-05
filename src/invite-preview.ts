export type InviteScope = {
  email: string;
  company_id: string;
  company_role: string;
  department_id: string | null;
};
export function validInvitePreview(value: unknown, expected: InviteScope[]): boolean {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  if (
    typeof v.ready !== 'boolean' ||
    typeof v.provisioning_enabled !== 'boolean' ||
    !Array.isArray(v.items) ||
    v.items.length !== expected.length
  )
    return false;
  return (
    v.ready === v.items.every((row) => row?.ready === true) &&
    v.items.every(
      (row, index) =>
        row &&
        row.index === index &&
        typeof row.ready === 'boolean' &&
        row.book_access === 'none' &&
        (!row.ready ||
          (row.email === expected[index].email.toLowerCase() &&
            row.company_id === expected[index].company_id &&
            row.company_role === expected[index].company_role &&
            row.department_id === expected[index].department_id)),
    )
  );
}
