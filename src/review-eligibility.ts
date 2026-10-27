import type { ReviewSnapshot } from './review-types';

/**
 * 复核资格的展示投影：服务端 issues 是唯一事实来源，这里只做归类、排序与
 * 「谁接着办」的说明。任何字段都不构成授权，提交时仍由服务端重检。
 */
export type ReviewBlockerCode =
  | 'period_closed'
  | 'self_prepared'
  | 'open_case'
  | 'duplicate_pending'
  | 'missing_evidence'
  | 'incomplete_lines'
  | 'unbalanced'
  | 'amount_mismatch'
  | 'line_side_error'
  | 'bank_direction'
  | 'account_inactive'
  | 'unknown';

export type ReviewBlocker = {
  code: ReviewBlockerCode;
  /** 受控中文说明：不回显服务端原文。 */
  reason: string;
  /** 下一位负责人（角色，不是身份断言）。 */
  owner: string;
  /** 下一步入口文案。 */
  entry: string;
  /** 服务端原始说明，仅供「查看原始提示」展开核对。 */
  source: string;
};

type Rule = {
  code: ReviewBlockerCode;
  match: RegExp;
  reason: string;
  owner: string;
  entry: string;
};

/** 顺序即优先级：期间、独立性、资料与分录问题按业务影响从大到小排列。 */
const rules: Rule[] = [
  {
    code: 'period_closed',
    match: /^该会计期间已关闭/,
    reason: '该会计期间已结账，当前不允许再审核凭证。',
    owner: '会计主管',
    entry: '联系会计主管确认是否需要按既定流程重开期间',
  },
  {
    code: 'self_prepared',
    match: /^申请人不能审核本人业务事项/,
    reason: '这张凭证由你本人制单，独立性要求不能由你复核。',
    owner: '独立复核会计',
    entry: '移交给另一位具备复核资格的会计审核',
  },
  {
    code: 'open_case',
    match: /尚未关闭的异常或补件/,
    reason: '关联的银行流水仍有关注中的异常或补件。',
    owner: '处理该异常的会计',
    entry: '先在异常与补件里处理完，再回来复核',
  },
  {
    code: 'duplicate_pending',
    match: /待核验的相似交易/,
    reason: '存在待核验的相似交易，需先确认不是重复记账。',
    owner: '银行流水核验人',
    entry: '先核验相似交易是否重复，再复核本张凭证',
  },
  {
    code: 'missing_evidence',
    match: /^缺少凭证证据引用/,
    reason: '凭证还没有绑定任何证据引用。',
    owner: '制单会计',
    entry: '补充资料与证据引用后重新预览',
  },
  {
    code: 'incomplete_lines',
    match: /^未形成完整分录/,
    reason: '分录还没有补全对应科目。',
    owner: '制单会计',
    entry: '补齐科目与金额后重新预览',
  },
  {
    code: 'unbalanced',
    match: /^借贷不平衡/,
    reason: '借方与贷方合计不相等。',
    owner: '制单会计',
    entry: '修正分录金额后重新预览',
  },
  {
    code: 'amount_mismatch',
    match: /^借贷合计必须分别等于原始流水金额/,
    reason: '借贷合计与原流水金额不一致。',
    owner: '制单会计',
    entry: '按原始流水金额更正分录',
  },
  {
    code: 'line_side_error',
    match: /^每行必须且只能有一侧非零金额/,
    reason: '某一行同时有借方和贷方金额，或两边都为零。',
    owner: '制单会计',
    entry: '每行只保留一侧金额后重新预览',
  },
  {
    code: 'bank_direction',
    match: /^银行存款分录的金额或方向与原始流水不符/,
    reason: '银行存款分录的金额或方向与原始流水对不上。',
    owner: '制单会计',
    entry: '核对收付方向与金额后更正',
  },
  {
    code: 'account_inactive',
    match: /^科目 .+ 不存在或已停用/,
    reason: '使用了不存在或已停用的科目。',
    owner: '制单会计',
    entry: '更换为有效科目后重新预览',
  },
];

const unknownBlocker = (source: string): ReviewBlocker => ({
  code: 'unknown',
  reason: '存在尚未归类的检查项，需要人工核对原始提示。',
  owner: '制单会计',
  entry: '打开凭证逐项核对，处理后重新预览',
  source,
});

/** 把服务端 issues 归类为去重的阻断项，保持服务端顺序。 */
export function reviewBlockers(issues: readonly string[]): ReviewBlocker[] {
  const seen = new Set<ReviewBlockerCode>();
  const blockers: ReviewBlocker[] = [];
  for (const source of issues) {
    const text = source.trim();
    if (!text) continue;
    const rule = rules.find((item) => item.match.test(text));
    const blocker: ReviewBlocker = rule
      ? {
          code: rule.code,
          reason: rule.reason,
          owner: rule.owner,
          entry: rule.entry,
          source: text,
        }
      : unknownBlocker(text);
    if (seen.has(blocker.code)) continue;
    seen.add(blocker.code);
    blockers.push(blocker);
  }
  return blockers;
}

export type ReviewAuthorization = 'granted' | 'denied' | 'unknown';

export type ReviewState = 'ready' | 'blocked' | 'denied' | 'expired' | 'stale' | 'unknown';

export type ReviewEligibility = {
  state: ReviewState;
  /** 当前状态一句话，用于徽标与标题。 */
  headline: string;
  blockers: ReviewBlocker[];
  /** 只有 ready 才为 true；权限未知或阻断时永远为 false。 */
  canConfirm: boolean;
  /** 下一位负责人。 */
  nextOwner: string;
  /** 下一步入口。 */
  nextEntry: string;
};

export type ReviewEligibilityInput = {
  /** granted 来自「这一本账套的读取被服务端接受」，不是全局角色。 */
  authorization: ReviewAuthorization;
  snapshot: Pick<ReviewSnapshot, 'issues' | 'ready' | 'expected_version'> | null;
  /** 预览是否已过期（服务端返回的 expires_at 已过）。 */
  previewExpired?: boolean;
  /** 用户以为自己在复核的草稿版本；与快照不一致说明期间被人改过。 */
  knownVersion?: number | null;
};

export function reviewEligibility(input: ReviewEligibilityInput): ReviewEligibility {
  const { authorization, snapshot, previewExpired = false, knownVersion = null } = input;
  if (authorization === 'unknown')
    return {
      state: 'unknown',
      headline: '复核资格尚未确认',
      blockers: [],
      canConfirm: false,
      nextOwner: '本人',
      nextEntry: '重新读取当前凭证与权限',
    };
  if (authorization === 'denied')
    return {
      state: 'denied',
      headline: '当前账号无权复核这本账套',
      blockers: [],
      canConfirm: false,
      nextOwner: '事务所管理员',
      nextEntry: '申请这本账套的复核授权后再试',
    };
  if (!snapshot)
    return {
      state: 'unknown',
      headline: '尚未读取到当前凭证',
      blockers: [],
      canConfirm: false,
      nextOwner: '本人',
      nextEntry: '重新读取当前凭证与权限',
    };
  if (previewExpired)
    return {
      state: 'expired',
      headline: '复核预览已过期',
      blockers: [],
      canConfirm: false,
      nextOwner: '本人',
      nextEntry: '重新预览当前凭证后再确认',
    };
  if (knownVersion != null && knownVersion !== snapshot.expected_version)
    return {
      state: 'stale',
      headline: '凭证在你核对后被修改',
      blockers: [],
      canConfirm: false,
      nextOwner: '本人',
      nextEntry: '重新读取当前凭证后再复核',
    };
  const blockers = reviewBlockers(snapshot.issues);
  if (blockers.length > 0)
    return {
      state: 'blocked',
      headline: `有 ${blockers.length} 项检查未通过`,
      blockers,
      canConfirm: false,
      nextOwner: blockers[0].owner,
      nextEntry: blockers[0].entry,
    };
  if (!snapshot.ready)
    return {
      state: 'blocked',
      headline: '服务端标记为不可确认',
      blockers: [unknownBlocker('服务端返回 ready=false 但未给出具体检查项')],
      canConfirm: false,
      nextOwner: '制单会计',
      nextEntry: '打开凭证核对后重新预览',
    };
  return {
    state: 'ready',
    headline: '可以复核',
    blockers: [],
    canConfirm: true,
    nextOwner: '本人（复核后进入导出）',
    nextEntry: '确认复核并生成回执',
  };
}
