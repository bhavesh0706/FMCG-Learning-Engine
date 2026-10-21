import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // Stricter rules from newer eslint-plugin-react-hooks. The existing
      // load()/setPage(1)/useAutoRefresh effects are deliberate, so these are
      // reported as warnings instead of failing CI. Findings stay visible.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      // Dialog files export helpers next to components; only affects fast refresh.
      'react-refresh/only-export-components': 'warn',
    },
  },
  {
    // Build-time config runs in Node, not the browser (fixes 'process' is not defined).
    files: ['vite.config.js'],
    languageOptions: {
      globals: globals.node,
    },
  },
])