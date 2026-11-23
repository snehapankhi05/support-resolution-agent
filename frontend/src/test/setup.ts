import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest without globals does not auto-wire RTL cleanup.
afterEach(() => {
  cleanup()
})
