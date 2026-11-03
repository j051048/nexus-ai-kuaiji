// Measures the actual Vite static dependency closure; vendor splitting cannot hide cost.
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, relative, dirname, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

export const deferredScreens = [
  'src/BookWorkspaceShell.tsx',
  'src/WorkspaceHome.tsx',
  'src/AppDialogs.tsx',
  'src/ClientAccessTools.tsx',
  'src/AdminWorkspace.tsx',
  'src/LegacyMigrationWorkspace.tsx',
];
const defaultLimits = {
  initialJs: 570000,
  initialGzip: 176000,
  homeJs: 625000,
  homeGzip: 195000,
  initialCss: 110000,
  homeCss: 145000,
};

export function inspectBuild(directory, { deferred = deferredScreens, limits = {} } = {}) {
  const root = realpathSync(directory);
  const manifestText = readFileSync(resolve(root, '.vite/manifest.json'), 'utf8');
  const manifest = JSON.parse(manifestText);
  if (!manifest['index.html']?.isEntry) throw new Error('Missing main entry');
  function closure(keys) {
    const visited = new Set();
    function visit(key) {
      if (visited.has(key)) return;
      const chunk = manifest[key];
      if (!chunk || typeof chunk.file !== 'string')
        throw new Error(`Missing manifest dependency: ${key}`);
      visited.add(key);
      for (const dependency of chunk.imports ?? []) visit(dependency);
    }
    keys.forEach(visit);
    return visited;
  }
  const hashes = new Map();
  function asset(file) {
    if (!/^assets\/[A-Za-z0-9_.\/-]+$/.test(file) || file.split('/').includes('..'))
      throw new Error('Invalid build asset');
    const path = realpathSync(resolve(root, file));
    const rel = relative(root, path);
    if (rel.startsWith('..') || isAbsolute(rel)) throw new Error('Invalid build asset');
    const content = readFileSync(path);
    hashes.set(file, createHash('sha256').update(content).digest('hex'));
    return { bytes: content.length, gzip: gzipSync(content).length };
  }
  function metrics(keys) {
    const js = new Set(),
      css = new Set();
    for (const key of keys) {
      js.add(manifest[key].file);
      for (const file of manifest[key].css ?? []) css.add(file);
    }
    let jsBytes = 0,
      jsGzipBytes = 0,
      cssBytes = 0;
    for (const file of js) {
      const size = asset(file);
      jsBytes += size.bytes;
      jsGzipBytes += size.gzip;
    }
    for (const file of css) cssBytes += asset(file).bytes;
    return { jsBytes, jsGzipBytes, cssBytes, jsFiles: [...js].sort(), cssFiles: [...css].sort() };
  }
  const initialKeys = closure(['index.html']);
  const homeKeys = closure([
    'index.html',
    ...(manifest['src/WorkspaceHome.tsx'] ? ['src/WorkspaceHome.tsx'] : []),
  ]);
  const initial = metrics(initialKeys),
    home = metrics(homeKeys),
    violations = [];
  for (const key of deferred) {
    if (!manifest[key]?.isDynamicEntry) violations.push(`missing-split:${key}`);
    if (initialKeys.has(key)) violations.push(`eager:${key}`);
  }
  const measured = {
    initialJs: initial.jsBytes,
    initialGzip: initial.jsGzipBytes,
    homeJs: home.jsBytes,
    homeGzip: home.jsGzipBytes,
    initialCss: initial.cssBytes,
    homeCss: home.cssBytes,
  };
  const budgets = { ...defaultLimits, ...limits };
  for (const key of Object.keys(measured))
    if (measured[key] > budgets[key]) violations.push(`budget:${key}`);
  return {
    schema: 'frontend-budget-v1',
    status: violations.length ? 'failed' : 'passed',
    initial,
    home,
    budgets,
    violations,
    artifactSha256: createHash('sha256')
      .update(manifestText)
      .update(JSON.stringify([...hashes].sort()))
      .digest('hex'),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = inspectBuild(process.argv[2] || 'dist');
    if (process.argv[3]) {
      mkdirSync(dirname(resolve(process.argv[3])), { recursive: true });
      writeFileSync(process.argv[3], JSON.stringify(result, null, 2) + '\n');
    }
    console.log(JSON.stringify(result, null, 2));
    process.exitCode = result.status === 'passed' ? 0 : 1;
  } catch (error) {
    console.error(`Frontend budget failed: ${error.message}`);
    process.exitCode = 1;
  }
}
