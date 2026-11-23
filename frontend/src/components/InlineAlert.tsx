import type { ReactNode } from 'react'

export type InlineAlertTone = 'danger' | 'warn'

type InlineAlertProps = {
  tone: InlineAlertTone
  /** Defaults: danger → alert, warn → status. */
  role?: 'alert' | 'status'
  children: ReactNode
  className?: string
}

const TONE_CLASS: Record<InlineAlertTone, string> = {
  danger: 'border-danger/20 bg-danger-soft text-danger',
  warn: 'border-warn/30 bg-warn-soft text-warn',
}

/**
 * One-line banner matching existing page error/warn chrome.
 */
export function InlineAlert({ tone, role, children, className = '' }: InlineAlertProps) {
  const resolvedRole = role ?? (tone === 'danger' ? 'alert' : 'status')
  const extra = className.trim()
  return (
    <p
      role={resolvedRole}
      className={`rounded-md border px-3 py-2 text-sm ${TONE_CLASS[tone]}${extra ? ` ${extra}` : ''}`}
    >
      {children}
    </p>
  )
}
