import { describe, expect, it } from 'vitest';
import { CSV_HEADERS, EXAMPLE_CSV, MAX_CSV_BYTES, formatCents, moneyToCents, parseCsv, toSourceRows } from './csv';

const header = CSV_HEADERS.join(',');
const row = 'bank:001,2026-10-01,办公用品,商店,0.10,expense,票据001';

describe('CSV 解析与导入边界', () => {
  it('读取 BOM、CRLF、引号内逗号、换行与双引号转义', () => {
    expect(parseCsv('\uFEFFa,b\r\n"第一行,\n第二行","他说""收到"""\r\n')).toEqual([
      ['a', 'b'], ['第一行,\n第二行', '他说"收到"'],
    ]);
    expect(toSourceRows(EXAMPLE_CSV)).toHaveLength(2);
  });

  it('保留末尾空字段，可稍后补充凭据', () => {
    const [source] = toSourceRows(`${header}\n${row.replace('票据001', '')}`);
    expect(source.evidence_ref).toBe('');
    expect(source.amount).toBe('0.10');
  });

  it.each(['a\n"缺少闭合', 'a\n"内容"尾部', 'a\n未转义"引号'])('拒绝含糊的引号结构 %s', (input) => {
    expect(() => parseCsv(input)).toThrow(/引号/);
  });

  it('拒绝缺失/重复表头及列数错位', () => {
    expect(() => toSourceRows(`date,amount\n2026-10-01,1`)).toThrow('缺少字段');
    expect(() => toSourceRows(`${header},amount\n${row},1`)).toThrow('重复字段');
    expect(() => toSourceRows(`${header}\n${row},额外值`)).toThrow('列数');
    expect(() => toSourceRows(header)).toThrow('只有表头');
  });

  it.each(['2026-02-30', '2026-13-01', '2026-1-01', '无日期'])('拒绝不存在或格式错误的日期 %s', (date) => {
    expect(() => toSourceRows(`${header}\n${row.replace('2026-10-01', date)}`)).toThrow('日期不正确');
  });

  it.each(['0', '-1', '001.00', '1.001', '1e3', 'NaN', '9999999999999', '￥100'])('拒绝不符合精确金额契约的输入 %s', (amount) => {
    expect(() => toSourceRows(`${header}\n${row.replace('0.10', amount)}`)).toThrow('金额必须');
  });

  it('拒绝批内重复流水编号与不合法的收支方向', () => {
    expect(() => toSourceRows(`${header}\n${row}\n${row}`)).toThrow('当前文件中重复');
    expect(() => toSourceRows(`${header}\n${row.replace('expense', '支出')}`)).toThrow('收支方向');
  });

  it('按 UTF-8 字节数限制大小，按非空记录限制行数', () => {
    expect(() => parseCsv('账'.repeat(Math.floor(MAX_CSV_BYTES / 3) + 1))).toThrow('2 MB');
    const thousand = Array.from({ length: 1000 }, (_, index) => row.replace('bank:001', `bank:${index}`)).join('\n');
    expect(toSourceRows(`${header}\n${thousand}`).length).toBe(1000);
    expect(() => toSourceRows(`${header}\n${thousand}\n${row}`)).toThrow('最多导入 1000');
  });

  it('与后端文本长度契约保持一致', () => {
    expect(() => toSourceRows(`${header}\n${row.replace('办公用品', '字'.repeat(501))}`)).toThrow('文本字段过长');
    expect(() => toSourceRows(`${header}\n${row.replace('商店', '字'.repeat(201))}`)).toThrow('文本字段过长');
  });
});

describe('人民币精确分位计算', () => {
  it('0.1 + 0.2 精确等于 0.30，大金额没有浮点误差', () => {
    expect(formatCents(moneyToCents('0.1') + moneyToCents('0.2'))).toBe('0.30');
    expect(formatCents(moneyToCents('999999999999.99'))).toBe('999,999,999,999.99');
    expect(formatCents(-1n)).toBe('-0.01');
  });
  it('非法金额不能被静默归零', () => {
    expect(() => moneyToCents('')).toThrow();
    expect(() => moneyToCents('0.001')).toThrow();
    expect(() => moneyToCents('01')).toThrow();
  });
});
