import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

type Link = {
  id: string;
  asset_id: string;
  vehicle_external_id: string;
  vehicle_name: string | null;
  source_version: number;
  status: 'active' | 'revoked';
  stale_reason: string | null;
  revoked_at: string | null;
  revoke_reason: string | null;
};

type Props = {
  session: Session;
  assetId: string;
  assetActive: boolean;
  onChanged?: () => void;
};

export function AssetVehicleLinkEditor({ session, assetId, assetActive, onChanged }: Props) {
  const [link, setLink] = useState<Link | null>(null);
  const [vehicle, setVehicle] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const sequence = useRef(0);

  const path = `/periodic/assets/${encodeURIComponent(assetId)}/vehicle-link`;

  useEffect(() => {
    const current = ++sequence.current;
    setNotice('');
    setError('');
    void api<{ link: Link | null }>(path, session)
      .then((body) => {
        if (current === sequence.current) setLink(body.link);
      })
      .catch((err: unknown) => {
        if (current === sequence.current) setError(errorMessage(err));
      });
  }, [path, session]);

  async function run(action: () => Promise<Link>, done: string) {
    const current = ++sequence.current;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const next = await action();
      if (current !== sequence.current) return;
      setLink(next);
      setNotice(done);
      setVehicle('');
      setReason('');
      onChanged?.();
    } catch (err: unknown) {
      if (current === sequence.current) setError(errorMessage(err));
    } finally {
      if (current === sequence.current) setBusy(false);
    }
  }

  const active = link?.status === 'active';
  return (
    <section className="asset-vehicle-link" aria-label="资产车辆关联">
      <div className="asset-vehicle-link-head">
        <strong>车辆关联</strong>
        <span className={`tax-rate-status ${active ? 'active' : 'draft'}`}>
          {active ? '关联生效' : link?.status === 'revoked' ? '已撤销' : '未关联'}
        </span>
      </div>
      {active ? (
        <>
          <p>
            已关联 {link.vehicle_name || link.vehicle_external_id} · 车辆编号{' '}
            {link.vehicle_external_id} · 车辆主数据 v{link.source_version}
          </p>
          {link.stale_reason && <p className="asset-vehicle-link-stale">{link.stale_reason}</p>}
          <div className="asset-vehicle-link-row">
            <input
              aria-label="撤销关联原因"
              value={reason}
              maxLength={500}
              placeholder="撤销原因（至少 3 字）"
              onChange={(event) => setReason(event.target.value)}
            />
            <button
              type="button"
              className="button secondary"
              disabled={busy || reason.trim().length < 3}
              onClick={() =>
                void run(
                  () =>
                    api<Link>(`${path}/revoke`, session, {
                      method: 'POST',
                      body: { reason: reason.trim() },
                    }),
                  '车辆关联已撤销；历史关联保留。',
                )
              }
            >
              撤销关联
            </button>
          </div>
        </>
      ) : (
        <>
          <p>
            {link?.status === 'revoked'
              ? `上次关联 ${link.vehicle_external_id} 已撤销，历史记录保留。`
              : '未关联车辆。车辆必须已在运输主数据中登记且启用。'}
          </p>
          <div className="asset-vehicle-link-row">
            <input
              aria-label="车辆外部编号"
              value={vehicle}
              maxLength={100}
              placeholder="车辆外部编号，如 VIN-001"
              onChange={(event) => setVehicle(event.target.value)}
            />
            <button
              type="button"
              className="button secondary"
              disabled={busy || !assetActive || !vehicle.trim()}
              onClick={() =>
                void run(
                  () =>
                    api<Link>(path, session, {
                      method: 'POST',
                      body: { vehicle_external_id: vehicle.trim() },
                    }),
                  '车辆关联已登记；折旧来源将绑定该车辆主数据版本。',
                )
              }
            >
              关联车辆
            </button>
          </div>
        </>
      )}
      {!assetActive && <p>已处置资产不能新关联车辆。</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
