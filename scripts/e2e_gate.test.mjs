import assert from 'node:assert/strict';
import test from 'node:test';
import { validateCombined, validateManifest, validateResults } from './e2e_gate.mjs';

const manifest = {
  version: 1,
  cases: [
    { file: 'e2e/one.spec.ts', title: 'desktop 来源恢复' },
    { file: 'e2e/nested/two.spec.ts', title: 'mobile › 带账套返回' },
  ],
};

function results() {
  return manifest.cases.map((identity) => ({
    ...identity,
    expectedStatus: 'passed',
    outcome: 'expected',
    attempts: [{ status: 'passed', retry: 0 }],
  }));
}

function metadata() {
  return {
    exitCode: 0,
    globalErrors: [],
    sourceBefore: 'candidate-fingerprint',
    sourceAfter: 'candidate-fingerprint',
    runId: 'this-run',
    reportRunId: 'this-run',
  };
}

function assertRejected(actual, code) {
  assert.equal(actual.passed, false);
  assert.ok(
    actual.issues.some((issue) => issue === code || issue.startsWith(`${code}: `)),
    actual.issues.join('\n'),
  );
}

test('manifest preserves the frozen cases without normalizing identities', () => {
  assert.equal(validateManifest(manifest), manifest.cases);
});

for (const [name, value, code] of [
  ['null', null, 'MANIFEST_INVALID_SCHEMA'],
  ['array', [], 'MANIFEST_INVALID_SCHEMA'],
  ['missing version', { cases: manifest.cases }, 'MANIFEST_INVALID_VERSION'],
  ['string version', { version: '1', cases: manifest.cases }, 'MANIFEST_INVALID_VERSION'],
  ['other version', { version: 2, cases: manifest.cases }, 'MANIFEST_INVALID_VERSION'],
  ['boolean version', { version: true, cases: manifest.cases }, 'MANIFEST_INVALID_VERSION'],
  ['missing cases', { version: 1 }, 'MANIFEST_INVALID_CASES'],
  ['null cases', { version: 1, cases: null }, 'MANIFEST_INVALID_CASES'],
  ['empty cases', { version: 1, cases: [] }, 'MANIFEST_EMPTY_CASES'],
  ['null case', { version: 1, cases: [null] }, 'MANIFEST_INVALID_CASE'],
  ['missing file', { version: 1, cases: [{ title: 'test' }] }, 'MANIFEST_INVALID_FILE'],
  [
    'absolute file',
    { version: 1, cases: [{ file: '/e2e/a.spec.ts', title: 'test' }] },
    'MANIFEST_INVALID_FILE',
  ],
  [
    'traversal file',
    { version: 1, cases: [{ file: 'e2e/../a.spec.ts', title: 'test' }] },
    'MANIFEST_INVALID_FILE',
  ],
  [
    'dot segment',
    { version: 1, cases: [{ file: 'e2e/./a.spec.ts', title: 'test' }] },
    'MANIFEST_INVALID_FILE',
  ],
  [
    'backslash file',
    { version: 1, cases: [{ file: 'e2e\\a.spec.ts', title: 'test' }] },
    'MANIFEST_INVALID_FILE',
  ],
  [
    'non spec file',
    { version: 1, cases: [{ file: 'e2e/a.ts', title: 'test' }] },
    'MANIFEST_INVALID_FILE',
  ],
  [
    'empty title',
    { version: 1, cases: [{ file: 'e2e/a.spec.ts', title: ' ' }] },
    'MANIFEST_INVALID_TITLE',
  ],
  [
    'numeric title',
    { version: 1, cases: [{ file: 'e2e/a.spec.ts', title: 1 }] },
    'MANIFEST_INVALID_TITLE',
  ],
  [
    'multiline title',
    { version: 1, cases: [{ file: 'e2e/a.spec.ts', title: 'test\nsecret' }] },
    'MANIFEST_INVALID_TITLE',
  ],
  [
    'duplicate identity',
    { version: 1, cases: [manifest.cases[0], manifest.cases[0]] },
    'MANIFEST_DUPLICATE_IDENTITY',
  ],
]) {
  test(`manifest rejects ${name} with a fixed reason`, () => {
    assert.throws(() => validateManifest(value), { message: code });
  });
}

test('complete first-attempt successful evidence passes', () => {
  assert.deepEqual(validateResults(manifest, results(), metadata()), { passed: true, issues: [] });
});

test('result order is irrelevant but identities remain exact', () => {
  assert.deepEqual(validateResults(manifest, results().reverse(), metadata()), {
    passed: true,
    issues: [],
  });
});

test('numeric zero global errors is accepted', () => {
  assert.equal(
    validateResults(manifest, results(), { ...metadata(), globalErrors: 0 }).passed,
    true,
  );
});

test('invalid manifest is a failing result rather than an exception', () => {
  assertRejected(validateResults(null, results(), metadata()), 'MANIFEST_INVALID_SCHEMA');
});

for (const value of [undefined, null, {}, false, 0]) {
  test(`non-array results are rejected (${String(value)})`, () => {
    assertRejected(validateResults(manifest, value, metadata()), 'RESULTS_INVALID');
  });
}

test('zero executed cases cannot pass', () => {
  assertRejected(validateResults(manifest, [], metadata()), 'RESULT_MISSING_IDENTITY');
});

test('one missing case cannot pass', () => {
  assertRejected(
    validateResults(manifest, results().slice(1), metadata()),
    'RESULT_MISSING_IDENTITY',
  );
});

test('duplicate case cannot replace a different required case', () => {
  const actual = validateResults(manifest, [results()[0], results()[0]], metadata());
  assertRejected(actual, 'RESULT_DUPLICATE_IDENTITY');
  assertRejected(actual, 'RESULT_MISSING_IDENTITY');
});

test('additional unknown case is rejected without exposing its identity or payload', () => {
  const extra = {
    file: 'e2e/unknown-secret.spec.ts',
    title: 'PRIVATE_TITLE',
    payload: 'PRIVATE_PAYLOAD',
    expectedStatus: 'PRIVATE_STATUS',
    outcome: 'PRIVATE_OUTCOME',
    attempts: [],
  };
  const actual = validateResults(manifest, [...results(), extra], metadata());
  assertRejected(actual, 'RESULT_UNEXPECTED_IDENTITY');
  assert.doesNotMatch(JSON.stringify(actual), /PRIVATE_|unknown-secret/);
});

for (const [name, mutate, code] of [
  [
    'skip',
    (row) => {
      row.outcome = 'skipped';
      row.attempts[0].status = 'skipped';
    },
    'OUTCOME_NOT_EXPECTED',
  ],
  [
    'flaky retry success',
    (row) => {
      row.outcome = 'flaky';
      row.attempts = [
        { status: 'failed', retry: 0 },
        { status: 'passed', retry: 1 },
      ];
    },
    'OUTCOME_NOT_EXPECTED',
  ],
  [
    'expected failure',
    (row) => {
      row.expectedStatus = 'failed';
      row.attempts[0].status = 'failed';
    },
    'EXPECTED_STATUS_NOT_PASSED',
  ],
  [
    'failed attempt with expected outcome',
    (row) => {
      row.attempts[0].status = 'failed';
    },
    'ATTEMPT_NOT_PASSED',
  ],
  [
    'interrupted attempt',
    (row) => {
      row.attempts[0].status = 'interrupted';
    },
    'ATTEMPT_NOT_PASSED',
  ],
  [
    'timeout attempt',
    (row) => {
      row.attempts[0].status = 'timedOut';
    },
    'ATTEMPT_NOT_PASSED',
  ],
  [
    'no attempts',
    (row) => {
      row.attempts = [];
    },
    'ATTEMPT_COUNT_NOT_ONE',
  ],
  [
    'multiple successful attempts',
    (row) => {
      row.attempts.push({ status: 'passed', retry: 0 });
    },
    'ATTEMPT_COUNT_NOT_ONE',
  ],
  [
    'null attempts',
    (row) => {
      row.attempts = null;
    },
    'ATTEMPTS_INVALID',
  ],
  [
    'null attempt',
    (row) => {
      row.attempts = [null];
    },
    'ATTEMPT_INVALID',
  ],
  [
    'missing retry',
    (row) => {
      delete row.attempts[0].retry;
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'retry one',
    (row) => {
      row.attempts[0].retry = 1;
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'retry null',
    (row) => {
      row.attempts[0].retry = null;
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'retry false',
    (row) => {
      row.attempts[0].retry = false;
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'retry string',
    (row) => {
      row.attempts[0].retry = '0';
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'retry NaN',
    (row) => {
      row.attempts[0].retry = NaN;
    },
    'ATTEMPT_RETRY_NOT_ZERO',
  ],
  [
    'missing expected status',
    (row) => {
      delete row.expectedStatus;
    },
    'EXPECTED_STATUS_NOT_PASSED',
  ],
  [
    'missing outcome',
    (row) => {
      delete row.outcome;
    },
    'OUTCOME_NOT_EXPECTED',
  ],
]) {
  test(`execution evidence rejects ${name}`, () => {
    const rows = results();
    mutate(rows[0]);
    assertRejected(validateResults(manifest, rows, metadata()), code);
  });
}

for (const value of [null, false, {}, []]) {
  test(`malformed result entry fails (${JSON.stringify(value)})`, () => {
    assertRejected(
      validateResults(manifest, [value, ...results()], metadata()),
      'RESULT_IDENTITY_INVALID',
    );
  });
}

test('known identity may appear in issues but result payload and status values never do', () => {
  const rows = results();
  Object.assign(rows[0], {
    expectedStatus: 'SECRET_EXPECTED',
    outcome: 'SECRET_OUTCOME',
    payload: 'SECRET_BODY',
  });
  rows[0].attempts = [{ status: 'SECRET_STATUS', retry: 'SECRET_RETRY' }];
  const actual = validateResults(manifest, rows, metadata());
  assert.ok(actual.issues.some((issue) => issue.includes('e2e/one.spec.ts::desktop 来源恢复')));
  assert.doesNotMatch(JSON.stringify(actual), /SECRET_/);
});

for (const value of [undefined, null, false, [], 0]) {
  test(`missing or malformed metadata fails (${String(value)})`, () => {
    assertRejected(validateResults(manifest, results(), value), 'META_INVALID');
  });
}

for (const [field, value, code] of [
  ['exitCode', 1, 'EXIT_CODE_NOT_ZERO'],
  ['exitCode', null, 'EXIT_CODE_NOT_ZERO'],
  ['exitCode', false, 'EXIT_CODE_NOT_ZERO'],
  ['exitCode', '0', 'EXIT_CODE_NOT_ZERO'],
  ['exitCode', NaN, 'EXIT_CODE_NOT_ZERO'],
  ['globalErrors', ['PRIVATE_GLOBAL_ERROR'], 'GLOBAL_ERRORS_PRESENT'],
  ['globalErrors', 1, 'GLOBAL_ERRORS_PRESENT'],
  ['globalErrors', null, 'GLOBAL_ERRORS_INVALID'],
  ['globalErrors', false, 'GLOBAL_ERRORS_INVALID'],
  ['globalErrors', NaN, 'GLOBAL_ERRORS_INVALID'],
  ['globalErrors', -1, 'GLOBAL_ERRORS_INVALID'],
  ['globalErrors', 0.5, 'GLOBAL_ERRORS_INVALID'],
  ['globalErrors', '0', 'GLOBAL_ERRORS_INVALID'],
  ['sourceBefore', '', 'SOURCE_INVALID'],
  ['sourceAfter', ' ', 'SOURCE_INVALID'],
  ['sourceBefore', null, 'SOURCE_INVALID'],
  ['sourceAfter', false, 'SOURCE_INVALID'],
  ['sourceAfter', 'PRIVATE_DIFFERENT_SOURCE', 'SOURCE_CHANGED'],
  ['runId', '', 'RUN_ID_INVALID'],
  ['runId', null, 'RUN_ID_INVALID'],
  ['reportRunId', false, 'RUN_ID_INVALID'],
  ['reportRunId', 'PRIVATE_OLD_RUN', 'REPORT_RUN_ID_MISMATCH'],
]) {
  test(`metadata rejects ${field}=${String(value)}`, () => {
    const actual = validateResults(manifest, results(), { ...metadata(), [field]: value });
    assertRejected(actual, code);
    assert.doesNotMatch(JSON.stringify(actual), /PRIVATE_/);
  });
}

for (const field of Object.keys(metadata())) {
  test(`metadata requires ${field}`, () => {
    const meta = metadata();
    delete meta[field];
    assert.equal(validateResults(manifest, results(), meta).passed, false);
  });
}

function combinedInputs() {
  return [
    { mode: 'dev', passed: true, source: 'candidate-fingerprint', runId: 'dev-run' },
    { mode: 'preview', passed: true, source: 'candidate-fingerprint', runId: 'preview-run' },
  ];
}

test('combined gate accepts two successful modes of the same source with distinct runs', () => {
  assert.deepEqual(validateCombined(...combinedInputs()), { passed: true, issues: [] });
});

for (const [name, mutate, code] of [
  [
    'missing preview',
    (pair) => {
      pair[1] = undefined;
    },
    'COMBINED_INPUT_INVALID',
  ],
  [
    'reversed modes',
    (pair) => {
      pair.reverse();
    },
    'COMBINED_MODE_INVALID',
  ],
  [
    'duplicate dev mode',
    (pair) => {
      pair[1].mode = 'dev';
    },
    'COMBINED_MODE_INVALID',
  ],
  [
    'failed dev',
    (pair) => {
      pair[0].passed = false;
    },
    'COMBINED_MODE_NOT_PASSED',
  ],
  [
    'truthy passed string',
    (pair) => {
      pair[1].passed = 'true';
    },
    'COMBINED_MODE_NOT_PASSED',
  ],
  [
    'missing source',
    (pair) => {
      delete pair[0].source;
    },
    'COMBINED_SOURCE_INVALID',
  ],
  [
    'null source',
    (pair) => {
      pair[1].source = null;
    },
    'COMBINED_SOURCE_INVALID',
  ],
  [
    'blank source',
    (pair) => {
      pair[1].source = ' ';
    },
    'COMBINED_SOURCE_INVALID',
  ],
  [
    'different source',
    (pair) => {
      pair[1].source = 'PRIVATE_SOURCE';
    },
    'COMBINED_SOURCE_MISMATCH',
  ],
  [
    'missing run id',
    (pair) => {
      delete pair[0].runId;
    },
    'COMBINED_RUN_ID_INVALID',
  ],
  [
    'boolean run id',
    (pair) => {
      pair[1].runId = false;
    },
    'COMBINED_RUN_ID_INVALID',
  ],
  [
    'reused run',
    (pair) => {
      pair[1].runId = pair[0].runId;
    },
    'COMBINED_RUN_ID_REUSED',
  ],
]) {
  test(`combined gate rejects ${name}`, () => {
    const pair = combinedInputs();
    mutate(pair);
    const actual = validateCombined(...pair);
    assertRejected(actual, code);
    assert.doesNotMatch(JSON.stringify(actual), /PRIVATE_/);
  });
}
