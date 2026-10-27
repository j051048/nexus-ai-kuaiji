/**
 * 纯函数式读取闸门：把「作用域切换 / 卸载 / 连续重试」的竞态收敛到一处，
 * 组件只判断 ticket 是否仍被接受，不再各自手写 epoch 与 AbortController。
 *
 * 它只决定「这个响应还能不能落到状态里」，不做任何授权判断。
 */
export type ReadTicket = { stamp: number; signal: AbortSignal };

export interface ReadGuard {
  /** 开始一次读取：立即中止上一笔在途请求，并发放新票据。 */
  begin(): ReadTicket;
  /** 这笔响应是否仍是当前读取（且未被释放）。 */
  accepts(ticket: ReadTicket): boolean;
  /** 释放当前作用域；迟到的 abort 不得影响已经接替它的新读取。 */
  cancel(ticket?: ReadTicket): void;
  /** 作用域是否已释放且尚未开始新的读取。 */
  released(): boolean;
}

export function createReadGuard(): ReadGuard {
  let latest = 0;
  let live: { stamp: number; controller: AbortController } | null = null;
  let released = false;

  return {
    begin() {
      live?.controller.abort();
      const controller = new AbortController();
      latest += 1;
      live = { stamp: latest, controller };
      released = false;
      return { stamp: latest, signal: controller.signal };
    },
    accepts(ticket) {
      return !released && !!live && live.stamp === ticket.stamp && !ticket.signal.aborted;
    },
    cancel(ticket) {
      if (ticket && live && ticket.stamp !== live.stamp) return;
      live?.controller.abort();
      live = null;
      released = true;
    },
    released() {
      return released;
    },
  };
}
