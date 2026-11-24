/**
 * 低优先级辅助子面板请求调度器：
 * 浏览器同源 HTTP/1.1 并发槽位仅 6 个；税务底稿下挂 10 个辅助专题面板时，
 * 仅对初始被动挂载的后台读取限制在最多 2 个并发槽内；当存在用户显式操作、
 * 刷新或任务直达高优请求在途时暂停派发新的后台任务，始终为主工作区交互
 * 保留连接槽与后端处理能力，并原生支持 AbortSignal 在排队阶段即时取消。
 */
const MAX_CONCURRENT = 2;

type Entry = {
  run: () => void;
  cancel: () => void;
};

let active = 0;
let highPriorityInFlight = 0;
const queue: Entry[] = [];

function pump() {
  while (highPriorityInFlight === 0 && active < MAX_CONCURRENT && queue.length > 0) {
    const next = queue.shift();
    next?.run();
  }
}

export function scheduleSubpanel<T>(
  task: () => Promise<T>,
  signal?: AbortSignal,
  priority: 'background' | 'high' = 'background',
): Promise<T> {
  if (signal?.aborted) {
    return Promise.reject(new DOMException('辅助面板读取已取消', 'AbortError'));
  }
  // 用户显式点击操作/刷新（不带被动挂载 AbortSignal）或从待办任务直达的高优焦点面板，
  // 直接发起请求并暂缓派发新后台低优读取，避免抢占后端连接与写锁。
  if (!signal || priority === 'high') {
    highPriorityInFlight += 1;
    return Promise.resolve()
      .then(task)
      .finally(() => {
        highPriorityInFlight = Math.max(0, highPriorityInFlight - 1);
        pump();
      });
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
