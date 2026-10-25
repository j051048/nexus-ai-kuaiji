import { createHash, randomUUID } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { validateManifest, validateResults, validateCombined } from './e2e_gate.mjs';
import { safeResults, buildFingerprint, sameBuild } from './e2e_reporter.mjs';

export function playwrightCLI() {
  const packagePath = createRequire(import.meta.url).resolve('playwright/package.json');
  const metadata = JSON.parse(readFileSync(packagePath, 'utf8'));
  return join(dirname(packagePath), metadata.bin.playwright);
}

export function parseModes(args) {
  if (args.length !== 2 || args[0] !== '--mode' || !['dev', 'preview', 'both'].includes(args[1])) {
    throw new Error('Use --mode dev|preview|both; selection overrides are not allowed.');
  }
  return args[1] === 'both' ? ['dev', 'preview'] : [args[1]];
}

export function sourceFingerprint(root) {
  const files = execFileSync('git', ['ls-files', '-c', '-o', '--exclude-standard', '-z'], {
    cwd: root,
  })
    .toString('utf8')
    .split('\0')
    .filter(Boolean);
  const hash = createHash('sha256');
  for (const file of [...new Set(files)].sort()) {
    hash.update(file).update('\0');
    // A tracked deletion is part of the candidate too.
    try {
      hash.update(readFileSync(join(root, file)));
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      hash.update('<deleted>');
    }
    hash.update('\0');
  }
  return hash.digest('hex');
}

export function readRunResult(path, runId, mode) {
  try {
    const report = JSON.parse(readFileSync(path, 'utf8'));
    const valid =
      report.runId === runId &&
      report.mode === mode &&
      Number.isSafeInteger(report.globalErrors) &&
      report.globalErrors >= 0 &&
      Array.isArray(report.results);
    return valid ? { valid, report } : { valid: false };
  } catch {
    return { valid: false };
  }
}

async function runProcess(cli, env, root, outDir) {
  return await new Promise((resolveExit) => {
    const child = spawn(
      process.execPath,
      [cli, 'test', '--config', 'playwright.config.ts', '--output', outDir],
      {
        cwd: root,
        env,
        stdio: 'inherit',
        windowsHide: true,
      },
    );
    child.once('error', () => resolveExit(1));
    child.once('exit', (code) => resolveExit(code ?? 1));
  });
}

export async function main(args) {
  const modes = parseModes(args);
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const manifestPath = join(root, 'scripts/e2e_core_cases.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8').replace(/^\uFEFF/, ''));
  validateManifest(manifest);
  const source = sourceFingerprint(root);
  const suiteId = randomUUID();
  const folder = join(root, 'test-reports/e2e-core', suiteId);
  const reports = join(folder, 'reports');
  mkdirSync(reports, { recursive: true });
  const cli = playwrightCLI();
  const summaries = [];
  console.log(`Core acceptance: ${manifest.cases.length} frozen cases; ${modes.join(' + ')}`);
  for (const mode of modes) {
    const runId = randomUUID();
    const runtime = join(folder, 'runtime', mode);
    const temp = join(runtime, 'temp');
    mkdirSync(temp, { recursive: true });
    const resultPath = join(runtime, 'result.json');
    const before = sourceFingerprint(root);
    let exitCode = 1;
    if (before === source) {
      exitCode = await runProcess(
        cli,
        {
          ...process.env,
          E2E_CORE_ACCEPTANCE: '1',
          E2E_FRONTEND_MODE: mode,
          E2E_GATE_RUN_ID: runId,
          E2E_GATE_RESULT: resultPath,
          TEMP: temp,
          TMP: temp,
          TMPDIR: temp,
        },
        root,
        join(runtime, 'browser'),
      );
    }
    const loaded = readRunResult(resultPath, runId, mode);
    const result = loaded.valid ? loaded.report : { results: [], globalErrors: 1 };
    const after = sourceFingerprint(root);
    const validation = validateResults(manifest, result.results, {
      exitCode,
      globalErrors: result.globalErrors,
      sourceBefore: source,
      sourceAfter: after,
      runId,
      reportRunId: loaded.valid ? result.runId : '',
    });
    let buildSha256 = null;
    if (mode === 'preview' && validation.passed) {
      try {
        buildSha256 = buildFingerprint(root);
        if (!sameBuild(result.buildBefore, buildSha256)) {
          validation.passed = false;
          validation.issues.push('BUILD_CHANGED_OR_UNBOUND');
        }
      } catch {
        validation.passed = false;
        validation.issues.push('build_artifact_missing');
      }
    }
    const summary = {
      version: 1,
      mode,
      runId,
      source,
      manifestSha256: createHash('sha256').update(readFileSync(manifestPath)).digest('hex'),
      passed: validation.passed,
      issues: validation.issues,
      exitCode,
      expected: manifest.cases.length,
      buildSha256,
      buildBeforeSha256: sameBuild(result.buildBefore, result.buildBefore)
        ? result.buildBefore
        : null,
      results: safeResults(manifest, result.results),
    };
    summaries.push(summary);
    writeFileSync(join(reports, `${mode}.json`), JSON.stringify(summary, null, 2), { flag: 'wx' });
    console.log(
      `${mode}: ${validation.passed ? 'PASS' : 'FAIL'}; ${validation.issues.length} gate issues`,
    );
  }
  const combined =
    modes.length === 2
      ? validateCombined(...summaries)
      : {
          passed: summaries[0].passed,
          issues: summaries[0].issues,
        };
  writeFileSync(
    join(reports, 'summary.json'),
    JSON.stringify(
      {
        version: 1,
        suiteId,
        source,
        modes,
        expectedPerMode: manifest.cases.length,
        passed: combined.passed,
        issues: combined.issues,
        boundary:
          'Local SQLite and synthetic identities only. No production, real AI, or PostgreSQL authorization acceptance.',
      },
      null,
      2,
    ),
    { flag: 'wx' },
  );
  console.log(`Sanitized evidence: ${reports}`);
  return combined.passed ? 0 : 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch(() => {
      console.error('Core gate could not start or finish. No passing evidence was produced.');
      process.exitCode = 1;
    });
}
