import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, realpathSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, sep } from 'node:path';
import { inspectBuild } from './frontend_budget.mjs';

function fixture(t, mutate = () => {}) {
  const tempRoot = realpathSync(tmpdir());
  const root = mkdtempSync(join(tempRoot, 'nexus-frontend-budget-'));
  t.after(() => {
    assert.ok(realpathSync(root).startsWith(`${tempRoot}${sep}nexus-frontend-budget-`));
    rmSync(root, { recursive: true, force: true });
  });
  mkdirSync(join(root, '.vite'));
  mkdirSync(join(root, 'assets'));
  const manifest = {
    'index.html': {
      file: 'assets/entry.js',
      isEntry: true,
      imports: ['shared'],
      dynamicImports: ['src/WorkspaceHome.tsx'],
      css: ['assets/global.css'],
    },
    shared: { file: 'assets/shared.js', imports: ['index.html'] },
    'src/WorkspaceHome.tsx': { file: 'assets/home.js', imports: ['shared'], isDynamicEntry: true },
  };
  mutate(manifest);
  for (const [name, text] of Object.entries({
    'entry.js': 'const a=1;',
    'shared.js': 'const b=1;',
    'home.js': 'const c=1;',
    'global.css': 'body{}',
  }))
    writeFileSync(join(root, 'assets', name), text);
  writeFileSync(join(root, '.vite/manifest.json'), JSON.stringify(manifest));
  return root;
}

test('counts transitive static assets once, separately from home-only code', (t) => {
  const result = inspectBuild(fixture(t), { deferred: ['src/WorkspaceHome.tsx'] });
  assert.equal(result.initial.jsBytes, 20);
  assert.equal(result.home.jsBytes, 30);
  assert.equal(result.initial.cssBytes, 6);
  assert.deepEqual(result.violations, []);
  assert.match(result.artifactSha256, /^[a-f0-9]{64}$/);
});
test('rejects reintroducing a deferred screen into the static entry graph', (t) => {
  const result = inspectBuild(
    fixture(t, (m) => m['index.html'].imports.push('src/WorkspaceHome.tsx')),
    { deferred: ['src/WorkspaceHome.tsx'] },
  );
  assert.ok(result.violations.includes('eager:src/WorkspaceHome.tsx'));
});
test('requires actual split output, not just a dynamic import declaration', (t) => {
  const result = inspectBuild(fixture(t), { deferred: ['src/BookWorkspaceShell.tsx'] });
  assert.ok(result.violations.includes('missing-split:src/BookWorkspaceShell.tsx'));
});
test('both raw and gzip limits are enforced', (t) => {
  const result = inspectBuild(fixture(t), {
    deferred: [],
    limits: { initialJs: 1, initialGzip: 1 },
  });
  assert.ok(result.violations.includes('budget:initialJs'));
  assert.ok(result.violations.includes('budget:initialGzip'));
});
test('missing static dependencies cannot silently reduce the measured size', (t) => {
  assert.throws(
    () => inspectBuild(fixture(t, (m) => m['index.html'].imports.push('missing'))),
    /Missing manifest dependency/,
  );
});
test('out-of-build paths are rejected', (t) => {
  assert.throws(
    () =>
      inspectBuild(
        fixture(t, (m) => {
          m.shared.file = '../private.js';
        }),
      ),
    /Invalid build asset/,
  );
});
