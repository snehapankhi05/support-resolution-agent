/**
 * Thin wrapper — production rules live in src/lib/casePolling.ts.
 * Usage (from frontend/): node scripts/check-case-polling.mjs
 */
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const vitestCli = join(root, 'node_modules', 'vitest', 'vitest.mjs')
const result = spawnSync(
  process.execPath,
  [vitestCli, 'run', 'src/lib/casePolling.test.ts'],
  { cwd: root, stdio: 'inherit' },
)

process.exit(result.status ?? 1)
