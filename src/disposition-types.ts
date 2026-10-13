import type { Draft } from './types';

export type DispositionOverview = {
  source_id: string; inactive: boolean; can_isolate: boolean; expected_version: number | null;
  disposition: { id: string; canonical_source_id: string; reason: string; created_at: string } | null;
};

export function canIsolateDuplicate(draft: Pick<Draft, 'version' | 'status'>, value: DispositionOverview | null) {
  return !!value && value.can_isolate && !value.inactive && draft.status === 'draft' && draft.version === value.expected_version;
}

export function sameTransactionCommand(expectedVersion: number, sourceFingerprint: string, canonicalFingerprint: string, reason: string, key: string) {
  const trimmed = reason.trim();
  if (trimmed.length < 3 || trimmed.length > 1000) throw new Error('请填写 3–1000 字的核验依据');
  if (!/^[a-f0-9]{64}$/.test(sourceFingerprint) || !/^[a-f0-9]{64}$/.test(canonicalFingerprint)) throw new Error('来源事实尚未加载，请刷新');
  return { expected_version: expectedVersion, source_fingerprint: sourceFingerprint, canonical_fingerprint: canonicalFingerprint, reason: trimmed, idempotency_key: key };
}
