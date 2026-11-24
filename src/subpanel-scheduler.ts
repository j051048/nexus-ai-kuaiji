/**
 * 低优先级辅助子面板请求调度器：
 * 浏览器同源 HTTP/1.1 并发槽位仅 6 个；税务底稿下挂 10 个辅助专题面板时，
 * 将辅助面板的初始后台读取限制在最多 2 个并发槽内，始终为主工作区交互
 * （档案保存、重新读取、独立复核、导出）保留 4 个空闲连接槽，并原生支持
 * AbortSignal 在排队阶段即时取消（避免 StrictMode 双挂和切账套泄漏）。
 */
const MAX_CONCURRENT = 2;

type Entry = {
  run: () => void;
  cancel: () => void;
};

let active = 0;
const queue: Entry[] = [];

function pump() {
  while (active < MAX_CONCURRENT && queue.length > 0) {
    const next = queue.shift();
    next?.run();
  }
}

export function scheduleSubpanel<T>(task: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) {
    return Promise.reject(new DOMException('辅助面板读取已取消', 'AbortError'));
  }
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const onAbort = () => {
      if (settled) return;
      settled = true;
      const index = queue.indexOf(entry);
      if (index >= 0) queue.splice(index, 1);
      reject(new DOMException('辅助面板读取已取消', 'AbortError'));
    };
    const entry: Entry = {
      cancel: onAbort,
      run: () => {
        if (settled || signal?.aborted) {
          onAbort();
          pump();
          return;
        }
        signal?.removeEventListener('abort', onAbort);
        active += 1;
        Promise.resolve()
          .then(task)
          .then(
            (value) => {
              if (!settled) {
                settled = true;
                resolve(value);
              }
            },
            (error) => {
              if (!settled) {
                settled = true;
                reject(error);
              }
            },
          )
          .finally(() => {
            active -= 1;
            pump();
          });
      },
    };
    signal?.addEventListener('abort', onAbort, { once: true });
    queue.push(entry);
    pump();
  });
}
