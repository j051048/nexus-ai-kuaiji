import { useId } from 'react';
import './term-help.css';

const explanations = {
  occurred_on: {
    question: '发生日期是什么？',
    meaning:
      '填写这件业务实际发生的日期，不是今天填写表单的日期。应结合原始资料核对，日期不确定时先向财务确认。',
    example: '虚构示例：10 月 12 日出差打车，10 月 15 日提交报销，发生日期填写 10 月 12 日。',
  },
  counterparty: {
    question: '交易对方是什么？',
    meaning:
      '与企业发生这笔业务的供应商、客户或实际收款人。按原件和真实业务填写，不确定时先补充说明，不猜名称。',
    example:
      '虚构示例：向青禾文具店采购办公用品，交易对方填写青禾文具店，不填写负责提交表单的员工姓名。',
  },
  payment_status: {
    question: '企业付款状态是什么？',
    meaning:
      '这里核对企业是否已付款，只记录事实，不会发起付款。员工垫付不等于企业已付款；报销事项仍由财务核对企业是否已向员工报销。',
    example:
      '虚构示例：员工先支付 80 元交通费，企业尚未报销，仍是“待企业付款”。已有支付依据时请交由财务核对，避免重复报销。',
  },
  draft: {
    question: '凭证草稿是什么？',
    meaning:
      '根据业务资料生成的候选会计记录，需会计核对原件、金额和会计处理。草稿不代表已记账，也不能直接当作已审核凭证导出。',
    example:
      '虚构示例：收到一张办公用品票据，系统先提出费用分录；会计核对业务及原件并审核后，才能继续现有导出流程。识别结果不代表票据已验真。',
  },
  debit_credit: {
    question: '借方和贷方是什么？',
    meaning:
      '借方与贷方是复式记账的两个方向，增减含义由科目性质决定，不能直接当成收入和支出。科目和分录应由授权会计核对。',
    example:
      '虚构示例：企业从银行提取现金，现金科目借方增加、银行存款贷方减少；这笔资金转移本身不是经营收入或费用。',
  },
  export_receipt: {
    question: '导出和回执有什么区别？',
    meaning:
      '导出是生成待交付的凭证文件，不代表外部财务软件已接收，也不等于已报税。回执用于核对外部系统的实际导入结果与差异。',
    example:
      '虚构示例：会计导出 CSV 后，仍须在交付流程核验导入回执；结果未知时先查已有记录，不要重复发送。',
  },
} as const;

export type HelpTerm = keyof typeof explanations;

/** Static explanations only. Opening help never reads or changes business data. */
export function TermHelp({ term, descriptionId }: { term: HelpTerm; descriptionId?: string }) {
  const instance = useId();
  const questionId = `${instance}-question`;
  const contentId = descriptionId ?? `${instance}-explanation`;
  const explanation = explanations[term];
  return (
    <details className="term-help">
      <summary id={questionId} aria-controls={contentId}>
        <span className="term-help-symbol" aria-hidden="true">
          ?
        </span>
        <span>{explanation.question}</span>
      </summary>
      <div id={contentId} className="term-help-content" aria-labelledby={questionId}>
        <p>{explanation.meaning}</p>
        <p className="term-help-example">{explanation.example}</p>
      </div>
    </details>
  );
}
