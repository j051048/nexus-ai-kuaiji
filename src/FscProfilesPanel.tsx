import { useEffect, useState } from 'react';
import { api } from './api';
import type { Session } from './types';

type Row = {
  company_id: string;
  company_name: string;
  status: 'missing' | 'draft' | 'confirmed';
  version: number | null;
  industry: string | null;
  unresolved: string[];
  confirmed_at: string | null;
};

type Matrix = { items: Row[]; total: number; confirmed: number; unresolved: number };

const STATUS: Record<Row['status'], string> = {
  missing: '未登记',
  draft: '待确认',
  confirmed: '已确认',
};

export function FscProfilesPanel({ session }: { session: Session }) {
  const [data, setData] = useState<Matrix | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api<Matrix>('/fsc/profiles/matrix', session)
      .then((body) => body)
      .then((body) => {
        if (!alive) return;
        setError('');
        setData(body);
      })
      .catch((cause) => {
        if (!alive || cause?.name === 'AbortError') return;
        setError('行业档案矩阵读取失败');
      });
    return () => {
      alive = false;
    };
  }, [session]);

  if (error)
    return (
      <p className="fsc-profiles-error" role="alert">
        {error}
      </p>
    );
  if (!data) return <p className="fsc-profiles-loading">正在读取五行业档案…</p>;
  return (
    <section className="fsc-profiles" aria-label="五行业档案矩阵">
      <header>
        <div>
          <h2>五行业档案</h2>
          <p>主体、核算权威、期间、税务、来源系统和留存口径必须逐项确认；未知不等于不适用。</p>
        </div>
        <strong>
          {data.confirmed}/{data.total} 已确认
        </strong>
      </header>
      <div className="fsc-profiles-table-wrap">
        <table>
          <thead>
            <tr>
              <th>企业</th>
              <th>行业</th>
              <th>状态</th>
              <th>版本</th>
              <th>未确认栏目</th>
              <th>确认时间</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((row) => (
              <tr key={row.company_id}>
                <td>{row.company_name}</td>
                <td>{row.industry ?? '—'}</td>
                <td>
                  <span className={`fsc-status ${row.status}`}>{STATUS[row.status]}</span>
                </td>
                <td>{row.version ? `v${row.version}` : '—'}</td>
                <td>
                  {row.status === 'missing'
                    ? '未登记（12 栏待填）'
                    : row.unresolved.length
                      ? row.unresolved.join('、')
                      : '—'}
                </td>
                <td>{row.confirmed_at ? row.confirmed_at.slice(0, 10) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fsc-profiles-note">
        只读投影；确认需由事务所管理员提交 12 栏责任人和依据，系统不代替客户确认。
      </p>
    </section>
  );
}
