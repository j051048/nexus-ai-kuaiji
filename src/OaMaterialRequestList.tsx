import { useEffect, useState } from 'react';
import { api } from './api';
import type { Session } from './types';
import './oa-material-requests.css';

type MaterialRequest = {
  id: string;
  title: string;
  reason: string;
  status: string;
  version: number;
  company_name: string;
  book_name: string;
};

export function OaMaterialRequestList({ session }: { session: Session }) {
  const [items, setItems] = useState<MaterialRequest[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void api<{ items: MaterialRequest[] }>('/oa/material-requests', session, {
      signal: controller.signal,
    })
      .then((body) => {
        setError('');
        setItems(body.items.filter((item) => item.status === 'open'));
      })
      .catch((cause) => {
        if (cause?.name === 'AbortError') return;
        setItems([]);
        setError('补件材料请求读取失败');
      });
    return () => controller.abort();
  }, [session]);

  return (
    <section className="oa-material-requests" aria-label="补件材料请求">
      <header>
        <div>
          <h2>补件材料请求</h2>
          <p>来自已核定的 OA 审批事实；只补材料，不重放或代审 OA。</p>
        </div>
      </header>
      {error ? (
        <p className="oa-material-error" role="alert">
          {error}
        </p>
      ) : null}
      {items.length ? (
        <div className="oa-material-list">
          {items.map((item) => (
            <article key={item.id}>
              <strong>{item.title}</strong>
              <p>{item.reason}</p>
              <small>
                {item.company_name} · {item.book_name} · 待补材料 · 版本 {item.version}
              </small>
            </article>
          ))}
        </div>
      ) : (
        <p className="oa-material-empty">当前没有待补材料请求。</p>
      )}
    </section>
  );
}
