import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', '.codex/**', '.agents/**', 'artifacts/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
    },
    plugins: { '@typescript-eslint': tseslint.plugin, 'react-hooks': reactHooks },
    rules: {
      'constructor-super': 'error',
      'no-constant-condition': ['error', { checkLoops: false }],
      'no-debugger': 'error',
      'no-duplicate-case': 'error',
      'no-unexpected-multiline': 'error',
      'no-unsafe-finally': 'error',
      'no-unreachable': 'error',
      'valid-typeof': 'error',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
);
