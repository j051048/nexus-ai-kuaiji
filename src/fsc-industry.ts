export type FscIndustryKind =
  | 'taxi'
  | 'advertising'
  | 'hospital'
  | 'logistics'
  | 'property'
  | 'general';

export interface IndustryProfile {
  kind: FscIndustryKind;
  label: string;
  badge: string;
  summary: string;
  /** 本业态专属核算重点（10% 差异化动态卡片） */
  accountingFocus: string[];
  /** 本业态涉税与月结关键提醒 */
  taxReminders: string[];
  /** 本业态对外行领导可直观汇报的核心经营指标 */
  kpiHints: string[];
}

export const INDUSTRY_PROFILES: Record<FscIndustryKind, IndustryProfile> = {
  taxi: {
    kind: 'taxi',
    label: '客运出租车',
    badge: '出租车业态插件',
    summary: '聚焦单车承包费（份子钱）、交通规费、车队燃油维保保险与单车净贡献核算。',
    accountingFocus: [
      '单车承包费 / 份子钱按车牌号逐月核对实收与欠缴台账',
      '交通规费、审验费、交强险与商业险按月分摊至对应运营车辆',
      '车队燃油、维修保养与事故理赔差异按单车归集成本',
    ],
    taxReminders: [
      '核对出租车客运收入发票与定额票开具完整性',
      '逐月复核车辆规费与保险摊销底稿后再转入月末结账',
    ],
    kpiHints: ['在营车辆数', '单车月均净贡献', '维保及保险占比'],
  },
  advertising: {
    kind: 'advertising',
    label: '文化传媒广告',
    badge: '广告传媒业态插件',
    summary: '聚焦项目合同毛利跟踪、媒介投放采购进项专票催收与文化事业建设费核算。',
    accountingFocus: [
      '按客户项目合同归集收入与外部媒介投放、设计制作成本',
      '重点跟踪大额媒介采购进项专票回票进度，防止多缴增值税',
      '核对预收广告款与按投放排期确认收入的跨期切分',
    ],
    taxReminders: [
      '计提文化事业建设费（含可扣除的合规媒介投放分包凭证）',
      '逐笔核对广告发布与制作合同印花税基数',
    ],
    kpiHints: ['重点项目毛利率', '媒介进项缺票金额', '项目回款账龄'],
  },
  hospital: {
    kind: 'hospital',
    label: '内部医疗（非对外）',
    badge: '内部医院业态插件',
    summary: '聚焦医药耗材领用核对、集团内部职工诊疗结算与医疗免税备案管理。',
    accountingFocus: [
      '药品、卫材采购入库与各科室领用消耗按月盘点核对',
      '与集团后勤集采公司的内部调拨医药款单独标注以便合并抵销',
      '内部职工体检与诊疗结算按集团分摊规则准确挂账',
    ],
    taxReminders: [
      '核查非营利/内部医疗服务增值税免税政策备案资料',
      '医疗免税项目对应进项税额须按规定准确转出',
    ],
    kpiHints: ['医药耗材月消耗额', '内部集采结算对账率', '科室费用结余'],
  },
  logistics: {
    kind: 'logistics',
    label: '后勤集采商贸物流',
    badge: '后勤集采业态插件',
    summary: '作为集团集中采购内部调拨核心源头，清晰区分内部供货对账与少量对外商贸进销。',
    accountingFocus: [
      '严格区分“集团内部调拨（供内部医院/物业/车队）”与“对外商贸销售”两套往来',
      '月末与各内部收货子公司逐笔核对内部应收/应付，确保合并抵销零差额',
      '仓储配送运费、装卸劳务与商贸进销存成本按月结转',
    ],
    taxReminders: [
      '核对对外商贸购销合同印花税与进销项税率匹配',
      '大宗物资集采专票验真与抵扣勾选期限预警',
    ],
    kpiHints: ['集团内部集采供货额', '对外商贸毛利率', '内部往来未达差额'],
  },
  property: {
    kind: 'property',
    label: '物业服务',
    badge: '物业服务业态插件',
    summary: '聚焦预收物业费按月权责分摊、水电代收代付净额核对与保洁安保外包劳务管理。',
    accountingFocus: [
      '按季度/年度预收的物业费严禁一次性计入当月收入，须按月分摊结转',
      '代收代付水电暖气费通过往来科目净额核对，避免虚增收入与税负',
      '保洁、绿化、安保外包劳务费逐月核对考勤验收单与进项发票',
    ],
    taxReminders: [
      '核对物业服务（6%）与代收水电差额开票口径一致性',
      '核查外包劳务公司专票及用工合规证明',
    ],
    kpiHints: ['当月应摊物业费收入', '物业费收缴率', '水电代收代付差额'],
  },
  general: {
    kind: 'general',
    label: '通用商贸服务',
    badge: '通用核算模式',
    summary: '采用集团共享财务中心标准通用核算主干，支持常规报销、往来核销与月度报税。',
    accountingFocus: [
      '核对当期原始发票、原办公审批单与银行收付款流水一致性',
      '按月完成往来账龄清理与费用科目归类复核',
    ],
    taxReminders: ['按月完成增值税进销项核对、附加税及企业所得税预缴底稿复核'],
    kpiHints: ['本月总收入', '本月总支出', '本月净结余'],
  },
};

export const ALL_INDUSTRY_OPTIONS: { kind: FscIndustryKind; label: string }[] = [
  { kind: 'taxi', label: '客运出租车' },
  { kind: 'advertising', label: '文化传媒广告' },
  { kind: 'hospital', label: '内部医疗（非对外）' },
  { kind: 'logistics', label: '后勤集采商贸物流' },
  { kind: 'property', label: '物业服务' },
  { kind: 'general', label: '通用商贸服务' },
];

const INDUSTRY_MAP_KEY = 'hengzhang:fsc:company-industry-map:v1';

export function readCompanyIndustryMap(): Record<string, FscIndustryKind> {
  try {
    const raw = window.localStorage.getItem(INDUSTRY_MAP_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const clean: Record<string, FscIndustryKind> = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (typeof v === 'string' && Object.prototype.hasOwnProperty.call(INDUSTRY_PROFILES, v)) {
        clean[k] = v as FscIndustryKind;
      }
    }
    return clean;
  } catch {
    return {};
  }
}

export function saveCompanyIndustry(companyId: string, kind: FscIndustryKind): void {
  try {
    const current = readCompanyIndustryMap();
    current[companyId] = kind;
    window.localStorage.setItem(INDUSTRY_MAP_KEY, JSON.stringify(current));
  } catch {
    // 忽略无存储环境
  }
}

/** 按配置台显式指定优先，未配置时依据公司名关键词精准匹配 5 大试点业态，绝不乱挂无关行业。 */
export function resolveCompanyIndustry(
  companyId?: string | null,
  companyName?: string | null,
  explicitMap?: Record<string, FscIndustryKind>,
): IndustryProfile {
  const map = explicitMap ?? readCompanyIndustryMap();
  if (companyId && map[companyId]) {
    return INDUSTRY_PROFILES[map[companyId]];
  }
  const name = (companyName ?? '').trim();
  if (/出租|客运|车队|运输|网约/.test(name)) return INDUSTRY_PROFILES.taxi;
  if (/广告|传媒|文化|营销|设计/.test(name)) return INDUSTRY_PROFILES.advertising;
  if (/医院|医疗|卫生|诊所|保健/.test(name)) return INDUSTRY_PROFILES.hospital;
  if (/后勤|采购|物流|供应链|集采|商贸/.test(name)) return INDUSTRY_PROFILES.logistics;
  if (/物业|园区|保洁|房管/.test(name)) return INDUSTRY_PROFILES.property;
  return INDUSTRY_PROFILES.general;
}
