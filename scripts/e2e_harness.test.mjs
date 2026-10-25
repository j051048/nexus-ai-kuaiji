import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { parseModes, sourceFingerprint, readRunResult, playwrightCLI } from './run_e2e.mjs';
import { testRecord, safeResults, buildFingerprint, sameBuild } from './e2e_reporter.mjs';
import { validateResults } from './e2e_gate.mjs';

test('preview must bind the same nonempty build before and after browser execution', () => {
  const dir = mkdtempSync(join(tmpdir(), 'v10-build-'));
  try {
    assert.throws(() => buildFingerprint(dir));
    mkdirSync(join(dir, 'dist'));
    writeFileSync(join(dir, 'dist', 'index.html'), '<script src="/app.js"></script>');
    writeFileSync(join(dir, 'dist', 'app.js'), 'first');
    const before = buildFingerprint(dir);
    assert.equal(sameBuild(before, buildFingerprint(dir)), true);
    writeFileSync(join(dir, 'dist', 'app.js'), 'replaced while testing');
    assert.equal(sameBuild(before, buildFingerprint(dir)), false);
    assert.equal(sameBuild(null, null), false);
    assert.equal(sameBuild('', ''), false);
  } finally {
    rmSync(dir, { recursive: true });
  }
});

test('real Playwright exit zero cannot hide expected failures, skipped tests or retry success', () => {
  mkdirSync('.codex', { recursive: true });
  const dir = mkdtempSync(join(process.cwd(), '.codex/v10-harness-'));
  mkdirSync(join(dir, 'e2e'));
  const reporter = join(process.cwd(), 'scripts/e2e_reporter.mjs').replaceAll('\\', '/');
  writeFileSync(
    join(dir, 'playwright.config.mjs'),
    `export default {
    testDir:'./e2e', workers:1, reporter:[[${JSON.stringify(reporter)}]], retries:0
  };`,
  );
  writeFileSync(
    join(dir, 'e2e/gate.spec.ts'),
    `import {test,expect} from '@playwright/test';
    test('good',()=>expect(true).toBe(true));
    test('expected failure',()=>{test.fail();expect(true).toBe(false);});
    test.skip('skipped',()=>{});
    test.describe('retry group',()=>{
      test.describe.configure({retries:1});
      test('retry success',({},info)=>expect(info.retry).toBe(1));
    });
  `,
  );
  const path = join(dir, 'result.json');
  // No page fixture, server, network or model: these are assertion-only tests.
  execFileSync(process.execPath, [playwrightCLI(), 'test'], {
    cwd: dir,
    stdio: 'pipe',
    env: {
      ...process.env,
      E2E_GATE_RESULT: path,
      E2E_GATE_RUN_ID: 'fixture',
      E2E_FRONTEND_MODE: 'dev',
    },
  });
  const loaded = readRunResult(path, 'fixture', 'dev');
  assert.equal(loaded.valid, true);
  const manifest = {
    version: 1,
    cases: ['good', 'expected failure', 'skipped', 'retry group › retry success'].map((title) => ({
      file: 'e2e/gate.spec.ts',
      title,
    })),
  };
  const checked = validateResults(manifest, loaded.report.results, {
    exitCode: 0,
    globalErrors: 0,
    sourceBefore: 'same',
    sourceAfter: 'same',
    runId: 'fixture',
    reportRunId: 'fixture',
  });
  assert.equal(checked.passed, false);
  assert.ok(checked.issues.some((x) => x.includes('EXPECTED_STATUS_NOT_PASSED')));
  assert.ok(checked.issues.some((x) => x.includes('OUTCOME_NOT_EXPECTED')));
  assert.ok(checked.issues.some((x) => x.includes('ATTEMPT_COUNT_NOT_ONE')));
});

test('installed Playwright CLI can be resolved without npx or network access', () => {
  assert.equal(existsSync(playwrightCLI()), true);
  assert.match(
    execFileSync(process.execPath, [playwrightCLI(), '--version']).toString(),
    /Version 1\./,
  );
});

test('only explicit dev, preview or both; no hidden selection overrides', () => {
  assert.deepEqual(parseModes(['--mode', 'both']), ['dev', 'preview']);
  assert.deepEqual(parseModes(['--mode', 'preview']), ['preview']);
  for (const args of [
    [],
    ['--mode', 'production'],
    ['--mode', 'dev', '--grep', 'foo'],
    ['--shard', '1/4'],
  ]) {
    assert.throws(() => parseModes(args));
  }
});

test('reporter retains describe identities and every attempt, but no data or error payload', () => {
  const fake = {
    title: 'task',
    location: { file: join(process.cwd(), 'e2e', 'example.spec.ts') },
    parent: {
      type: 'describe',
      title: 'scope',
      parent: { type: 'file', title: 'example.spec.ts' },
    },
    expectedStatus: 'failed',
    outcome: () => 'expected',
    results: [
      {
        status: 'failed',
        retry: 0,
        error: { message: 'synthetic-secret' },
        attachments: [{ body: 'document' }],
      },
    ],
  };
  const record = testRecord(fake, process.cwd());
  assert.equal(record.title, 'scope › task');
  assert.equal(record.file, 'e2e/example.spec.ts');
  assert.equal(record.expectedStatus, 'failed');
  assert.deepEqual(record.attempts, [{ status: 'failed', retry: 0 }]);
  assert.equal(JSON.stringify(record).includes('synthetic-secret'), false);
  assert.equal(JSON.stringify(record).includes('document'), false);
  const manifest = { version: 1, cases: [{ file: record.file, title: record.title }] };
  const projected = safeResults(manifest, [record, { file: 'secret-path', title: 'secret-body' }]);
  assert.equal(JSON.stringify(projected).includes('secret-'), false);
  assert.equal(projected.length, 1);
});

test('missing, damaged, foreign-run or malformed reports cannot be reused', () => {
  const dir = mkdtempSync(join(tmpdir(), 'v10-report-'));
  const path = join(dir, 'result.json');
  try {
    assert.equal(readRunResult(path, 'current', 'dev').valid, false);
    writeFileSync(path, '{');
    assert.equal(readRunResult(path, 'current', 'dev').valid, false);
    writeFileSync(
      path,
      JSON.stringify({ runId: 'old', mode: 'dev', globalErrors: 0, results: [] }),
    );
    assert.equal(readRunResult(path, 'current', 'dev').valid, false);
    writeFileSync(
      path,
      JSON.stringify({ runId: 'current', mode: 'preview', globalErrors: 0, results: [] }),
    );
    assert.equal(readRunResult(path, 'current', 'dev').valid, false);
    writeFileSync(
      path,
      JSON.stringify({ runId: 'current', mode: 'dev', globalErrors: 0, results: [] }),
    );
    assert.equal(readRunResult(path, 'current', 'dev').valid, true);
  } finally {
    rmSync(dir, { recursive: true });
  }
});

test('candidate fingerprint includes uncommitted and new inputs, excludes ignored outputs', () => {
  const dir = mkdtempSync(join(tmpdir(), 'v10-source-'));
  try {
    execFileSync('git', ['init', '-q'], { cwd: dir });
    writeFileSync(join(dir, '.gitignore'), 'artifacts/\n');
    writeFileSync(join(dir, 'source.ts'), 'first');
    execFileSync('git', ['add', '.'], { cwd: dir });
    const before = sourceFingerprint(dir);
    mkdirSync(join(dir, 'artifacts'));
    writeFileSync(join(dir, 'artifacts', 'fake.db'), 'not-source');
    assert.equal(sourceFingerprint(dir), before);
    writeFileSync(join(dir, 'source.ts'), 'changed');
    assert.notEqual(sourceFingerprint(dir), before);
    const edited = sourceFingerprint(dir);
    writeFileSync(join(dir, 'new.spec.ts'), 'new test');
    assert.notEqual(sourceFingerprint(dir), edited);
  } finally {
    rmSync(dir, { recursive: true });
  }
});
