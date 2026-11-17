import { useEffect, useState } from 'react';

import { api } from './api';
import type { Session } from './types';

type Envelope = {
  id: string;
  company_name: string;
  book_name: string;
  period: string;
  source_system: string;
  source_object: string;
  source_version: number;
  status: string;
  row_count: number;
  adopted_count: number;
  total_cents: number;
  sha256: string;
  created_at: string;
};

const STATUS: Record<string, string> = {
  draft: '草稿',
  confirmed: '已确认',
  superseded: '已被新版本替代',
};

function money(cents: number) {
  return `¥${(cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function FscImportPanel({ session }: { session: Session }) {
  const [items, setItems] = useState<Envelope[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    api<{ items: Envelope[] }>('/fsc/imports/recent', session)
      .then((body) => {
        if (!alive) return;
        setError('');
        setItems(body.items);
        setLoaded(true);
      })
      .catch((cause) => {
        if (!alive || cause?.name === 'AbortError') return;
        setError('导入信封读取失败');
      });
    return () => {
      alive = false;
    };
  }, [session]);

  if (error)
    return (
      <p className="fsc-org-error" role="alert">
        {error}
      </p>
    );
  if (!loaded) return <p className="fsc-org-loading">正在读取导入信封…</p>;
  return (
    <section className="fsc-import" aria-label="导入信封">
      <header>
        <div>
          <h2>导入信封</h2>
          <p>
            最近登记的外部来源与原件指纹。信封只描述来料，不代表已生成凭证、应收或外部系统已确认。
          </p>
        </div>
        <strong>{items.length} 份来料</strong>
      </header>
      {items.length === 0 ? (
        <p className="fsc-import-empty">尚无导入信封。行业来源须先登记版本、原件指纹和逐行状态。</p>
      ) : (
        <div className="fsc-import-table">
          <div className="fsc-import-head">
            <span>企业 / 账套</span>
            <span>来源 / 版本</span>
            <span>期间</span>
            <span>行数</span>
            <span>金额</span>
            <span>状态</span>
          </div>
          {items.map((item) => (
            <div className="fsc-import-row" key={item.id}>
              <strong>
                {item.company_name}
                <small>{item.book_name}</small>
              </strong>
              <span>
                {item.source_system} / {item.source_object} · v{item.source_version}
                <small>SHA {item.sha256.slice(0, 12)}…</small>
              </span>
              <span>{item.period}</span>
              <span>
                {item.adopted_count} / {item.row_count} 行
              </span>
              <span>{money(item.total_cents)}</span>
              <span>{STATUS[item.status] ?? item.status}</span>
            </div>
          ))}
        </div>
      )}
      <p className="fsc-import-note">
        未确认信封不会成为正式账务依据；已确认信封的内容不可修改，新版本另立并保留旧版本审计。
      </p>
    </section>
  );
}
