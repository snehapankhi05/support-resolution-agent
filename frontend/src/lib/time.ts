/** Relative waiting time for pending-queue display only. */
export function formatWaitingSince(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.max(0, Math.floor((now - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m waiting`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours}h waiting`
  const days = Math.floor(hours / 24)
  return `${days}d waiting`
}

export function formatCreatedAt(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('en-IN', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}
