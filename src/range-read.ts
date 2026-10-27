/**
 * Read lifecycle for the continuous-period books panel.
 *
 * A range read is bound to one request key (ledger, range and account). The
 * panel is a stub for late answers, revocations and fast A→B→A switching, so
 * every transition lives here and the component only renders the result.
 *
 * Two rules matter for auditability:
 * - switching the range clears what is on screen at once, so a stale balance
 *   can never sit next to newer dates;
 * - a lost grant clears the data as well, because a 403 means "this viewer may
 *   no longer see these numbers", not "the numbers are still fine".
 */

export type RangeReadEvent =
  | { type: 'switch'; key: string; ticket: number }
  | { type: 'ok'; key: string; ticket: number; data: unknown }
  | { type: 'fail'; key: string; ticket: number; message: string; status?: number }
  | { type: 'revoke'; message?: string }
  | { type: 'reset' };

export type RangeReadState = {
  key: string;
  ticket: number;
  status: 'idle' | 'loading' | 'ready' | 'failed';
  data: unknown | null;
  error: string;
  notice: string;
};

export const emptyRangeRead: RangeReadState = {
  key: '',
  ticket: 0,
  status: 'idle',
  data: null,
  error: '',
  notice: '',
};

export const REVOKED_TEXT =
  '权限已变更，当前账号不能再查看这段期间；请重新登录或联系事务所负责人重新授权后再试。';

export function rangeKey(ledgerId: string, start: string, end: string, accountId = ''): string {
  return [ledgerId, start, end, accountId].join('|');
}

export function canExport(state: RangeReadState, key: string): boolean {
  return state.status === 'ready' && state.data !== null && state.key === key;
}

export function reduceRead(state: RangeReadState, event: RangeReadEvent): RangeReadState {
  switch (event.type) {
    case 'switch':
      if (state.key === event.key && state.ticket === event.ticket) return state;
      return {
        key: event.key,
        ticket: event.ticket,
        status: 'loading',
        data: null,
        error: '',
        notice: '',
      };
    case 'ok':
      if (event.key !== state.key || event.ticket !== state.ticket) return state;
      return { ...state, status: 'ready', data: event.data, error: '' };
    case 'fail': {
      if (event.key !== state.key || event.ticket !== state.ticket) return state;
      const revoked = event.status === 401 || event.status === 403;
      return {
        ...state,
        status: 'failed',
        data: null,
        error: revoked ? REVOKED_TEXT : event.message,
        notice: '',
      };
    }
    case 'reset':
      return {
        ...state,
        ticket: state.ticket + 1,
        status: 'idle',
        data: null,
        error: '',
        notice: '',
      };
    case 'revoke':
      return {
        ...state,
        ticket: state.ticket + 1,
        status: 'failed',
        data: null,
        error: event.message ?? REVOKED_TEXT,
        notice: '',
      };
    default:
      return state;
  }
}
