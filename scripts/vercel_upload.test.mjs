import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const scratchRoot = join(root, '.codex');
const rules = readFileSync(join(root, '.vercelignore'), 'utf8');

function temporaryFolder() {
  mkdirSync(scratchRoot, { recursive: true });
  return mkdtempSync(join(scratchRoot, 'vercel-upload-'));
}

function cleanup(folder) {
  // Never remove a repository or a dependency target through a computed path.
  assert.ok(resolve(folder).startsWith(`${scratchRoot}${sep}vercel-upload-`));
  rmSync(folder, { recursive: true, force: true });
}

function uploadFiles(paths) {
  const matcher = temporaryFolder();
  try {
    execFileSync('git', ['init', '--quiet', matcher]);
    writeFileSync(join(matcher, '.gitignore'), rules);
    // .vercelignore uses gitignore rules. Let Git handle negations and ignored
    // parents instead of maintaining a partial glob implementation.
    const result = spawnSync(
      'git',
      ['-c', 'core.excludesFile=', 'check-ignore', '--no-index', '--stdin', '-z'],
      { cwd: matcher, input: `${paths.join('\0')}\0`, encoding: 'utf8' },
    );
    assert.ok([0, 1].includes(result.status), result.stderr || String(result.error));
    const ignored = new Set(result.stdout.split('\0').filter(Boolean));
    return paths.filter((path) => !ignored.has(path));
  } finally {
    cleanup(matcher);
  }
}

test('Vercel keeps the typecheck dependency while excluding private and operational files', () => {
  const retained = [
    'scripts/e2e_runtime.ts',
    'scripts/p02_identity_origin.ts',
    'src/App.tsx',
    'services/api_gateway/main.py',
  ];
  const excluded = [
    'scripts/migrate_accounting.py',
    'scripts/e2e_core_cases.json',
    'scripts/private/nested.ts',
    'nexus_backend/app/main.py',
    '.env',
    'config/.env.production',
    '.mcp.json',
    'nexus_backend/private-attachments/original.pdf',
    '.codex/private.json',
    '.venv/secret',
    'node_modules/example/index.js',
    'test-results/result.json',
    'database.dump',
  ];
  assert.deepEqual(uploadFiles([...retained, ...excluded]), retained);
});

test(
  'the filtered upload builds, and a missing helper fails the real build',
  { timeout: 180_000 },
  () => {
    const folder = temporaryFolder();
    try {
      const paths = execFileSync('git', ['ls-files', '-c', '-o', '--exclude-standard', '-z'], {
        cwd: root,
        encoding: 'utf8',
      });
      for (const file of uploadFiles([...new Set(paths.split('\0').filter(Boolean))])) {
        const destination = resolve(folder, file);
        assert.ok(destination.startsWith(`${folder}${sep}`));
        mkdirSync(dirname(destination), { recursive: true });
        copyFileSync(join(root, file), destination);
      }
      assert.equal(existsSync(join(folder, 'nexus_backend')), false);
      assert.equal(existsSync(join(folder, 'scripts/migrate_accounting.py')), false);
      symlinkSync(join(root, 'node_modules'), join(folder, 'node_modules'), 'junction');
      const npmCli = process.env.npm_execpath;
      assert.ok(npmCli && existsSync(npmCli), 'Run via npm run test:vercel-build');
      const runtimeTemp = join(folder, '.runtime-temp');
      mkdirSync(runtimeTemp);
      const env = {};
      for (const [key, value] of Object.entries(process.env)) {
        if (
          /^(PATH|PATHEXT|SYSTEMROOT|SYSTEMDRIVE|WINDIR|COMSPEC|HOME|USERPROFILE|TEMP|TMP|TMPDIR)$/i.test(
            key,
          )
        )
          env[key] = value;
      }
      Object.assign(env, {
        TEMP: runtimeTemp,
        TMP: runtimeTemp,
        TMPDIR: runtimeTemp,
        E2E_LOCAL_ISOLATION: '1',
        CI: 'true',
        NO_COLOR: '1',
        npm_config_audit: 'false',
        npm_config_fund: 'false',
        npm_config_update_notifier: 'false',
      });
      const build = () =>
        spawnSync(process.execPath, [npmCli, 'run', 'build'], {
          cwd: folder,
          env,
          encoding: 'utf8',
          timeout: 80_000,
          maxBuffer: 4 * 1024 * 1024,
        });
      const complete = build();
      assert.equal(
        complete.status,
        0,
        `${complete.stdout}\n${complete.stderr}\n${complete.error || ''}`,
      );
      assert.ok(existsSync(join(folder, 'dist/index.html')));
      // Guard against bypassing TypeScript to make the deployment green.
      rmSync(join(folder, 'scripts/e2e_runtime.ts'));
      const incomplete = build();
      assert.notEqual(incomplete.status, 0);
      assert.match(incomplete.stdout + incomplete.stderr, /TS2307[^\n]*scripts\/e2e_runtime/);
    } finally {
      cleanup(folder);
    }
  },
);
