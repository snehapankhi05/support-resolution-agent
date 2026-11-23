type StatusBadgeProps = {
  /** Visible label — caller formats any prefix (e.g. order status or "Ticket: open"). */
  label: string
}

/**
 * Neutral status chip — text + accent soft fill (not color-only).
 */
export function StatusBadge({ label }: StatusBadgeProps) {
  return (
    <span className="inline-flex rounded-md bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">
      {label}
    </span>
  )
}
