/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const srcDir = new URL('.', import.meta.url);
const tsxFiles = readdirSync(srcDir).filter((name) => name.endsWith('.tsx'));

describe('面向中国企业用户的母语化眉题守卫', () => {
  it('界面眉题（eyebrow / kicker）不残留全大写英文标语或内部开发编号', () => {
    const violations: string[] = [];
    for (const file of tsxFiles) {
      const content = readFileSync(new URL(file, srcDir), 'utf8');
      for (const match of content.matchAll(
        /className="[^"]*(?:eyebrow|kicker)[^"]*"[^>]*>([^<{]+)</g,
      )) {
        const text = match[1].trim();
        if (!text) continue;
        if (/^[A-Z0-9 /,&.-]{4,}$/.test(text) || /^TAX\d+/.test(text)) {
          violations.push(`${file}: ${text}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
