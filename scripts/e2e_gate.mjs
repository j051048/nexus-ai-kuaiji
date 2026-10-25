// The runner owns process execution and report provenance. This module validates
// normalized evidence and never copies unknown result data into its diagnostics.
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const nonemptyString = (value) => typeof value === 'string' && value.trim().length > 0;
const controlCharacters = /[\u0000-\u001f\u007f-\u009f]/u;
const identityKey = ({ file, title }) => JSON.stringify([file, title]);

function validFile(file) {
  if (
    !nonemptyString(file) ||
    file.trim() !== file ||
    controlCharacters.test(file) ||
    file.includes('\\') ||
    file.includes(':') ||
    !file.startsWith('e2e/') ||
    !file.endsWith('.spec.ts')
  )
    return false;
  return file.split('/').every((segment) => segment !== '' && segment !== '.' && segment !== '..');
}

function validTitle(title) {
  return nonemptyString(title) && !controlCharacters.test(title);
}

function fail(code) {
  throw new Error(code);
}

export function validateManifest(manifest) {
  if (!isRecord(manifest)) fail('MANIFEST_INVALID_SCHEMA');
  if (manifest.version !== 1) fail('MANIFEST_INVALID_VERSION');
  if (!Array.isArray(manifest.cases)) fail('MANIFEST_INVALID_CASES');
  if (manifest.cases.length === 0) fail('MANIFEST_EMPTY_CASES');
  const identities = new Set();
  for (const entry of manifest.cases) {
    if (!isRecord(entry)) fail('MANIFEST_INVALID_CASE');
    if (!validFile(entry.file)) fail('MANIFEST_INVALID_FILE');
    if (!validTitle(entry.title)) fail('MANIFEST_INVALID_TITLE');
    const key = identityKey(entry);
    if (identities.has(key)) fail('MANIFEST_DUPLICATE_IDENTITY');
    identities.add(key);
  }
  return manifest.cases;
}

const manifestFailureCodes = new Set([
  'MANIFEST_INVALID_SCHEMA',
  'MANIFEST_INVALID_VERSION',
  'MANIFEST_INVALID_CASES',
  'MANIFEST_EMPTY_CASES',
  'MANIFEST_INVALID_CASE',
  'MANIFEST_INVALID_FILE',
  'MANIFEST_INVALID_TITLE',
  'MANIFEST_DUPLICATE_IDENTITY',
]);

function conclusion(issues) {
  return { passed: issues.size === 0, issues: [...issues] };
}

export function validateResults(manifest, results, meta) {
  const issues = new Set();
  let cases;
  try {
    cases = validateManifest(manifest);
  } catch (error) {
    issues.add(
      manifestFailureCodes.has(error?.message) ? error.message : 'MANIFEST_INVALID_SCHEMA',
    );
    return conclusion(issues);
  }

  if (!isRecord(meta)) {
    issues.add('META_INVALID');
  } else {
    if (meta.exitCode !== 0) issues.add('EXIT_CODE_NOT_ZERO');
    if (Array.isArray(meta.globalErrors)) {
      if (meta.globalErrors.length !== 0) issues.add('GLOBAL_ERRORS_PRESENT');
    } else if (Number.isInteger(meta.globalErrors) && meta.globalErrors >= 0) {
      if (meta.globalErrors !== 0) issues.add('GLOBAL_ERRORS_PRESENT');
    } else {
      issues.add('GLOBAL_ERRORS_INVALID');
    }
    if (!nonemptyString(meta.sourceBefore) || !nonemptyString(meta.sourceAfter)) {
      issues.add('SOURCE_INVALID');
    } else if (meta.sourceBefore !== meta.sourceAfter) {
      issues.add('SOURCE_CHANGED');
    }
    if (!nonemptyString(meta.runId) || !nonemptyString(meta.reportRunId)) {
      issues.add('RUN_ID_INVALID');
    } else if (meta.runId !== meta.reportRunId) {
      issues.add('REPORT_RUN_ID_MISMATCH');
    }
  }

  if (!Array.isArray(results)) {
    issues.add('RESULTS_INVALID');
    return conclusion(issues);
  }
  const required = new Map(cases.map((entry) => [identityKey(entry), entry]));
  const seen = new Set();
  for (const result of results) {
    if (!isRecord(result) || !validFile(result.file) || !validTitle(result.title)) {
      issues.add('RESULT_IDENTITY_INVALID');
      continue;
    }
    const key = identityKey(result);
    const identity = required.get(key);
    if (!identity) {
      issues.add('RESULT_UNEXPECTED_IDENTITY');
      continue;
    }
    // The suffix comes exclusively from the validated frozen manifest.
    const issue = (code) => issues.add(`${code}: ${identity.file}::${identity.title}`);
    if (seen.has(key)) issue('RESULT_DUPLICATE_IDENTITY');
    seen.add(key);
    if (result.expectedStatus !== 'passed') issue('EXPECTED_STATUS_NOT_PASSED');
    if (result.outcome !== 'expected') issue('OUTCOME_NOT_EXPECTED');
    if (!Array.isArray(result.attempts)) {
      issue('ATTEMPTS_INVALID');
    } else if (result.attempts.length !== 1) {
      issue('ATTEMPT_COUNT_NOT_ONE');
    } else {
      const attempt = result.attempts[0];
      if (!isRecord(attempt)) {
        issue('ATTEMPT_INVALID');
      } else {
        if (attempt.status !== 'passed') issue('ATTEMPT_NOT_PASSED');
        if (attempt.retry !== 0) issue('ATTEMPT_RETRY_NOT_ZERO');
      }
    }
  }
  for (const [key, identity] of required) {
    if (!seen.has(key)) issues.add(`RESULT_MISSING_IDENTITY: ${identity.file}::${identity.title}`);
  }
  return conclusion(issues);
}

export function validateCombined(dev, preview) {
  const issues = new Set();
  if (!isRecord(dev) || !isRecord(preview)) {
    issues.add('COMBINED_INPUT_INVALID');
    return conclusion(issues);
  }
  if (dev.mode !== 'dev' || preview.mode !== 'preview') issues.add('COMBINED_MODE_INVALID');
  if (dev.passed !== true || preview.passed !== true) issues.add('COMBINED_MODE_NOT_PASSED');
  if (!nonemptyString(dev.source) || !nonemptyString(preview.source)) {
    issues.add('COMBINED_SOURCE_INVALID');
  } else if (dev.source !== preview.source) {
    issues.add('COMBINED_SOURCE_MISMATCH');
  }
  if (!nonemptyString(dev.runId) || !nonemptyString(preview.runId)) {
    issues.add('COMBINED_RUN_ID_INVALID');
  } else if (dev.runId === preview.runId) {
    issues.add('COMBINED_RUN_ID_REUSED');
  }
  return conclusion(issues);
}
