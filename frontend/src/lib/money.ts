/**
 * Format integer paise as INR display text (mirrors app.services.messages.format_inr).
 * Never accepts floats.
 */
export function formatPaiseAsInr(amountMinor: number): string {
  if (!Number.isInteger(amountMinor) || amountMinor < 0) {
    throw new TypeError('amountMinor must be a non-negative integer (paise)')
  }
  const rupees = Math.floor(amountMinor / 100)
  const paise = amountMinor % 100
  const whole = rupees.toLocaleString('en-IN')
  if (paise === 0) return `₹${whole}`
  return `₹${whole}.${String(paise).padStart(2, '0')}`
}

/** Show paid/refunded amounts; include currency code when not INR. */
export function formatOrderMoney(amountMinor: number, currency: string): string {
  const code = currency.trim().toUpperCase()
  const asInr = formatPaiseAsInr(amountMinor)
  if (code === 'INR' || code === '') return asInr
  return `${asInr} (${code})`
}
