import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { RecognizedDocument } from './IntelligencePanel';
import { OcrEvidenceViewer } from './OcrEvidenceViewer';
import {
  bindingLabel,
  boundBlocks,
  fieldBinding,
  ocrPreviewPath,
  ocrScore,
  polygonPoints,
} from './ocr-evidence';
import type { IndependentOcrEvidence, OcrDocument, OcrFieldBinding } from './intelligence-types';
import type { Session } from './types';

const session: Session = {
  mode: 'local',
  token: 'synthetic-only',
  user_id: 'user-a',
  user_name: '测试',
  firm_id: 'firm-a',
  firm_name: '测试',
  role: 'owner',
  scope_key: 'scope-a',
};
const evidence: IndependentOcrEvidence = {
  schema_version: 'independent-ocr-v1',
  engine: 'paddleocr',
  engine_version: 'fixture',
  model: 'fixture',
  preprocessing_version: 'paddle-input-v1',
  elapsed_ms: 200,
  recognized_pages: 1,
  native_pages: 1,
  pages: [
    {
      page: 1,
      width: 100,
      height: 200,
      source: 'paddleocr',
      blocks: [
        {
          id: 'p1-1',
          text: '金额<script>268.10</script>',
          score: 0.98,
          polygon: [
            [1, 2],
            [50, 2],
            [50, 20],
            [1, 20],
          ],
        },
      ],
    },
    { page: 2, width: 100, height: 200, source: 'pdf_native', blocks: [] },
  ],
  bindings: [
    {
      document_index: 0,
      field: 'gross_amount',
      page: 1,
      block_ids: ['p1-1'],
      score: 0.98,
      status: 'matched',
    },
  ],
};
const document: OcrDocument = {
  document_type: 'invoice',
  invoice_number: 'fixture',
  issued_on: '2026-10-14',
  seller_name: '虚构商户',
  seller_tax_id: null,
  buyer_name: null,
  buyer_tax_id: null,
  gross_amount: '268.10',
  net_amount: null,
  tax_amount: null,
  currency: 'CNY',
  pages: [{ page: 1, raw_text: '旧模型转录' }],
  evidence: [],
};

describe('独立文字证据与字段关联', () => {
  it('精确匹配文档索引和字段，不使用其他票据的同名字段', () => {
    expect(fieldBinding(evidence, 0, 'gross_amount')).toEqual(evidence.bindings[0]);
    expect(fieldBinding(evidence, 1, 'gross_amount')).toBeUndefined();
  });
  it('只从绑定页返回存在的文字块，未匹配状态不绘制框', () => {
    const binding = evidence.bindings[0];
    expect(boundBlocks(evidence.pages[0], binding)).toHaveLength(1);
    expect(boundBlocks(evidence.pages[1], binding)).toEqual([]);
    expect(boundBlocks(evidence.pages[0], { ...binding, status: 'unmatched' })).toEqual([]);
    expect(boundBlocks(evidence.pages[0], { ...binding, block_ids: ['missing'] })).toEqual([]);
  });
  it('定位路径由可信路由拼接，并编码来源和处理 ID', () => {
    expect(ocrPreviewPath('e/a', 'r?token=bad', 1)).toBe(
      '/business/events/e%2Fa/intelligence/runs/r%3Ftoken%3Dbad/pages/1',
    );
  });
  it('分数保留一位小数，异常值不变成0或100%', () => {
    expect(ocrScore(0.981)).toBe('98.1%');
    for (const score of [null, undefined, NaN, Infinity, -1, 1.001])
      expect(ocrScore(score)).toBe('未提供');
  });
  it('只允许留存图范围内的有限坐标，PDF文字层不伪造框', () => {
    const page = evidence.pages[0],
      block = page.blocks[0];
    expect(polygonPoints(block, page)).toBe('1,2 50,2 50,20 1,20');
    expect(polygonPoints(block, evidence.pages[1])).toBeNull();
    expect(
      polygonPoints(
        {
          ...block,
          polygon: [
            [-1, 2],
            [30, 2],
            [30, 5],
          ],
        },
        page,
      ),
    ).toBeNull();
    expect(
      polygonPoints(
        {
          ...block,
          polygon: [
            [1, 2],
            [Infinity, 2],
            [30, 5],
          ],
        },
        page,
      ),
    ).toBeNull();
    expect(
      polygonPoints(
        {
          ...block,
          polygon: [
            [1, 2],
            [130, 2],
            [30, 5],
          ],
        },
        page,
      ),
    ).toBeNull();
  });
  it.each(['low_confidence', 'unmatched'] as const)(
    '明确展示%s要求人工核对，不宣称会计正确率',
    (status) => {
      const binding: OcrFieldBinding = {
        ...evidence.bindings[0],
        status,
        score: status === 'unmatched' ? null : 0.5,
      };
      const html = renderToStaticMarkup(
        createElement(OcrEvidenceViewer, {
          session,
          eventId: 'e1',
          runId: 'r1',
          documentIndex: 0,
          evidence: { ...evidence, bindings: [binding] },
          selection: { field: 'gross_amount', page: 1 },
          onSelect: () => {},
        }),
      );
      expect(html).toContain(bindingLabel(binding));
      expect(html).toContain('请人工核对原件');
      expect(html).toContain('不代表会计处理正确率');
      expect(html).toContain('金额&lt;script&gt;');
      expect(html).not.toContain('<script>');
    },
  );
  it('PDF原文页无框时解释来源，不展示伪造文字识别分数', () => {
    const nativeBinding: OcrFieldBinding = {
      ...evidence.bindings[0],
      page: 2,
      block_ids: [],
      score: null,
    };
    const html = renderToStaticMarkup(
      createElement(OcrEvidenceViewer, {
        session,
        eventId: 'e1',
        runId: 'r1',
        documentIndex: 0,
        evidence: { ...evidence, bindings: [nativeBinding] },
        selection: { page: 2, field: 'gross_amount' },
        onSelect: () => {},
      }),
    );
    expect(html).toContain('本页使用 PDF 文字层校验，未生成文字定位框');
    expect(html).not.toContain('文字分数');
    expect(html).not.toContain('文字位置已标出');
    expect(html).not.toContain('字段文字识别分数');
  });
  it('旧视觉模型结果仍能查看原转录，不冒充独立文字证据', () => {
    const html = renderToStaticMarkup(createElement(RecognizedDocument, { document }));
    expect(html).toContain('旧模型转录');
    expect(html).not.toContain('独立文字识别证据');
    expect(html).not.toContain('文字识别分数');
  });
  it('新证据只为有值的字段提供定位入口，未知税额继续待核对', () => {
    const html = renderToStaticMarkup(
      createElement(RecognizedDocument, {
        document,
        ocrEvidence: evidence,
        previewContext: { session, eventId: 'e1', runId: 'r1' },
      }),
    );
    expect(html).toContain('核对价税合计的文字证据');
    expect(html).not.toContain('核对税额的文字证据');
    expect(html).toContain('待核对');
  });
});
