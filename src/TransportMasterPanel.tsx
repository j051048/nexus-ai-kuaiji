import { useCallback, useEffect, useState } from 'react';
import { Link2, RefreshCw, Truck } from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import type { MasterEntity } from './ledger-types';
import './transport-master.css';

const enc = encodeURIComponent;
const errorText = (failure: unknown) =>
  failure instanceof Error ? failure.message : '运输主数据未读取。';
type Subject = {
  id: string;
  kind: 'vehicle' | 'driver';
  external_id: string;
  display_name: string;
  active: boolean;
  version: number;
};
type Relation = {
  id: string;
  subject_id: string;
  subject_kind: string;
  target_kind: string;
  target_id: string;
  target_name: string;
  contract_external_id: string;
  operating_right: string;
  valid_from: string;
  valid_to: string | null;
  status: string;
};
type Assignment = {
  id: string;
  vehicle_id: string;
  driver_id: string;
  shift: string;
  contract_external_id: string;
  valid_from: string;
  valid_to: string | null;
};
type Alias = { id: string; subject_id: string; alias: string; source: string; status: string };
type Overview = {
  subjects: Subject[];
  relations: Relation[];
  assignments: Assignment[];
  aliases: Alias[];
};

export function TransportMasterPanel({
  session,
  bookId,
  entities,
  canManage,
}: {
  session: Session;
  bookId: string;
  entities: MasterEntity[];
  canManage: boolean;
}) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [kind, setKind] = useState<'vehicle' | 'driver'>('vehicle');
  const [externalId, setExternalId] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [targetId, setTargetId] = useState('');
  const [contract, setContract] = useState('');
  const [right, setRight] = useState('');
  const [from, setFrom] = useState('2026-10-01');
  const [vehicleId, setVehicleId] = useState('');
  const [driverId, setDriverId] = useState('');
  const [shift, setShift] = useState<'day' | 'night' | 'full'>('day');
  const [alias, setAlias] = useState('');
  const [aliasSource, setAliasSource] = useState('历史台账');
  const path = `/books/${enc(bookId)}/ledger/transport`;
  const load = useCallback(async () => {
    const result = await api<Overview>(path, session);
    setData(result);
    setError('');
  }, [path, session]);
  useEffect(() => {
    void load().catch((failure) => setError(errorText(failure)));
  }, [load]);
  const subject = data?.subjects.find((item) => item.id === subjectId);
  const targetKind = subject?.kind === 'driver' ? 'counterparty' : 'project';
  const targets = entities.filter((item) => item.kind === targetKind && item.active);
  async function run(action: () => Promise<string>) {
    setError('');
    setNotice('');
    try {
      setNotice(await action());
      await load();
    } catch (failure) {
      setError(errorText(failure));
    }
  }
  return (
    <section className="panel transport-master" aria-label="运输主数据">
      <div className="panel-heading">
        <div>
          <h2>
            <Truck size={18} />
            运输主数据
          </h2>
          <p>
            车辆和驾驶员用稳定外部 ID 识别；名称可重复，有效期决定当月适用关系。旧月份历史不覆盖。
          </p>
        </div>
        <button className="button secondary small" onClick={() => void load()}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {notice && <div className="form-notice">{notice}</div>}
      {data && (
        <>
          <div className="transport-summary">
            <span>{data.subjects.filter((item) => item.kind === 'vehicle').length} 辆车</span>
            <span>{data.subjects.filter((item) => item.kind === 'driver').length} 名驾驶员</span>
            <span>{data.relations.length} 条有效关系</span>
            <span>{data.assignments.length} 条班次</span>
          </div>
          <div className="transport-list">
            {data.subjects.length === 0 && (
              <p className="field-help">
                尚未建立车辆或驾驶员。先建稳定 ID，再挂接项目和往来主体。
              </p>
            )}
            {data.subjects.map((item) => {
              const relation = data.relations.find(
                (row) => row.subject_id === item.id && row.status === 'active',
              );
              const aliases = data.aliases
                .filter((row) => row.subject_id === item.id)
                .map((row) => row.alias);
              return (
                <article key={item.id}>
                  <strong>
                    {item.kind === 'vehicle' ? '车辆' : '驾驶员'} · {item.display_name}
                  </strong>
                  <small>
                    {item.external_id} · {item.active ? '启用' : '停用'} ·{' '}
                    {relation ? `→ ${relation.target_name}` : '未挂接'}
                    {aliases.length ? ` · 历史名 ${aliases.join('、')}` : ''}
                  </small>
                </article>
              );
            })}
          </div>
          {canManage && (
            <div className="transport-forms">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await api(`${path}/subjects`, session, {
                      method: 'POST',
                      body: { kind, external_id: externalId, display_name: displayName },
                    });
                    setExternalId('');
                    setDisplayName('');
                    return '运输主体已建立；关系与班次需要单独生效。';
                  });
                }}
              >
                <h3>建立车辆/驾驶员</h3>
                <label>
                  类型
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value as 'vehicle' | 'driver')}
                  >
                    <option value="vehicle">车辆</option>
                    <option value="driver">驾驶员</option>
                  </select>
                </label>
                <label>
                  稳定外部 ID
                  <input
                    required
                    value={externalId}
                    onChange={(e) => setExternalId(e.target.value)}
                    placeholder="VIN / 驾驶员编号"
                  />
                </label>
                <label>
                  显示名称
                  <input
                    required
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="车牌或姓名，可重复"
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={!externalId.trim() || !displayName.trim()}
                >
                  建立主体
                </button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await api(`${path}/relations`, session, {
                      method: 'POST',
                      body: {
                        subject_id: subjectId,
                        target_id: targetId,
                        contract_external_id: contract,
                        operating_right: right,
                        valid_from: from,
                        valid_to: null,
                      },
                    });
                    return '有效关系已记录；历史月份不受影响。';
                  });
                }}
              >
                <h3>挂接项目/往来主体</h3>
                <label>
                  运输主体
                  <select
                    required
                    value={subjectId}
                    onChange={(e) => {
                      setSubjectId(e.target.value);
                      setTargetId('');
                    }}
                  >
                    <option value="">请选择</option>
                    {data.subjects
                      .filter((item) => item.active)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.display_name} · {item.external_id}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  目标
                  <select required value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                    <option value="">请选择</option>
                    {targets.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  合同/经营权编号
                  <input required value={contract} onChange={(e) => setContract(e.target.value)} />
                </label>
                <label>
                  经营权/服务内容
                  <input required value={right} onChange={(e) => setRight(e.target.value)} />
                </label>
                <label>
                  生效日期
                  <input
                    type="date"
                    required
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={!subjectId || !targetId || !contract.trim() || !right.trim()}
                >
                  记录关系
                </button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await api(`${path}/assignments`, session, {
                      method: 'POST',
                      body: {
                        vehicle_id: vehicleId,
                        driver_id: driverId,
                        shift,
                        contract_external_id: contract,
                        valid_from: from,
                        valid_to: null,
                      },
                    });
                    return '班次已生效；同一驾驶员有效期重叠会被拒绝。';
                  });
                }}
              >
                <h3>车辆班次</h3>
                <label>
                  车辆
                  <select required value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
                    <option value="">请选择</option>
                    {data.subjects
                      .filter((item) => item.kind === 'vehicle' && item.active)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.display_name} · {item.external_id}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  驾驶员
                  <select required value={driverId} onChange={(e) => setDriverId(e.target.value)}>
                    <option value="">请选择</option>
                    {data.subjects
                      .filter((item) => item.kind === 'driver' && item.active)
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.display_name} · {item.external_id}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  班次
                  <select
                    value={shift}
                    onChange={(e) => setShift(e.target.value as 'day' | 'night' | 'full')}
                  >
                    <option value="day">白班</option>
                    <option value="night">夜班</option>
                    <option value="full">全天</option>
                  </select>
                </label>
                <label>
                  合同/经营权编号
                  <input required value={contract} onChange={(e) => setContract(e.target.value)} />
                </label>
                <label>
                  生效日期
                  <input
                    type="date"
                    required
                    value={from}
                    onChange={(e) => setFrom(e.target.value)}
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={!vehicleId || !driverId || !contract.trim()}
                >
                  记录班次
                </button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await api(`${path}/aliases`, session, {
                      method: 'POST',
                      body: { subject_id: subjectId, alias, source: aliasSource },
                    });
                    setAlias('');
                    return '历史名称已绑定；仅用于唯一匹配，不改变稳定 ID。';
                  });
                }}
              >
                <h3>历史名称</h3>
                <label>
                  运输主体
                  <select required value={subjectId} onChange={(e) => setSubjectId(e.target.value)}>
                    <option value="">请选择</option>
                    {data.subjects.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.display_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  历史车牌/名称
                  <input required value={alias} onChange={(e) => setAlias(e.target.value)} />
                </label>
                <label>
                  来源
                  <input
                    required
                    value={aliasSource}
                    onChange={(e) => setAliasSource(e.target.value)}
                  />
                </label>
                <button className="button secondary" disabled={!subjectId || !alias.trim()}>
                  绑定历史名
                </button>
              </form>
            </div>
          )}
          <p className="field-help">
            <Link2 size={13} />
            同一外部 ID 才视为同一主体；同名不合并。跨企业、跨项目冲突由服务端拒绝。
          </p>
        </>
      )}
    </section>
  );
}
