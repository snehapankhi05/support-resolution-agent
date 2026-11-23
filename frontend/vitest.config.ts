import { defineConfig } from 'vitest/config'

/**
 * Separate from vite.config.ts so Vite 8 plugin types are not mixed with the
 * Vite copy Vitest 3 nests under node_modules (tsc -b otherwise fails).
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
