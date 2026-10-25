import { writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { relative, join } from 'node:path';
import { createHash } from 'node:crypto';

export function buildFingerprint(root) {
  const folder = join(root, 'dist');
  readFileSync(join(folder, 'index.html'));
  const hash = createHash('sha256');
  function visit(dir, prefix = '') {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
      a.name.localeCompare(b.name),
    )) {
      const key = `${prefix}${entry.name}`;
      if (entry.isDirectory()) visit(join(dir, entry.name), `${key}/`);
      else
        hash
          .update(key)
          .update('\0')
          .update(readFileSync(join(dir, entry.name)))
          .update('\0');
    }
  }
  visit(folder);
  return hash.digest('hex');
}

export function sameBuild(before, after) {
  return typeof before === 'string' && /^[a-f0-9]{64}$/.test(before) && before === after;
}

// A whitelist: never retain errors, stdout, attachments, URLs or test bodies.
export function testRecord(test, root) {
  const titles = [test.title];
  for (let parent = test.parent; parent?.type === 'describe'; parent = parent.parent) {
    titles.unshift(parent.title);
  }
  return {
    file: relative(root, test.location.file).replaceAll('\\', '/'),
    title: titles.join(' › '),
    expectedStatus: test.expectedStatus,
    outcome: test.outcome(),
    attempts: test.results.map((result) => ({ status: result.status, retry: result.retry })),
  };
}

export function safeResults(manifest, results) {
  const statuses = new Set(['passed', 'failed', 'timedOut', 'interrupted', 'skipped']);
  const outcomes = new Set(['expected', 'unexpected', 'flaky', 'skipped']);
  return manifest.cases.map(({ file, title }) => {
    const matches = results.filter((item) => item?.file === file && item?.title === title);
    const record = matches.length === 1 ? matches[0] : null;
    return {
      file,
      title,
      matches: matches.length,
      expectedStatus: statuses.has(record?.expectedStatus) ? record.expectedStatus : 'invalid',
      outcome: outcomes.has(record?.outcome) ? record.outcome : 'invalid',
      attempts: Array.isArray(record?.attempts)
        ? record.attempts.map((attempt) => ({
            status: statuses.has(attempt?.status) ? attempt.status : 'invalid',
            retry:
              Number.isSafeInteger(attempt?.retry) && attempt.retry >= 0 ? attempt.retry : null,
          }))
        : [],
    };
  });
}

export default class CoreReporter {
  globalErrors = 0;
  buildBefore = null;
  onBegin(_config, suite) {
    this.suite = suite;
    // Playwright starts webServer (including build) before onBegin.
    if (process.env.E2E_FRONTEND_MODE === 'preview') {
      try {
        this.buildBefore = buildFingerprint(process.cwd());
      } catch {
        this.globalErrors += 1;
      }
    }
  }
  onError() {
    this.globalErrors += 1;
  }
  onEnd(result) {
    writeFileSync(
      process.env.E2E_GATE_RESULT,
      JSON.stringify(
        {
          runId: process.env.E2E_GATE_RUN_ID,
          mode: process.env.E2E_FRONTEND_MODE,
          buildBefore: this.buildBefore,
          globalErrors: this.globalErrors + (result.status === 'passed' ? 0 : 1),
          results: (this.suite?.allTests() || []).map((test) => testRecord(test, process.cwd())),
        },
        null,
        2,
      ),
      { flag: 'wx' },
    );
  }
}
