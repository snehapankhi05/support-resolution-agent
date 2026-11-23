/** Catalog names for display. Null when the API omitted them or the order has none. */
export function productNamesText(names: string[] | undefined): string | null {
  if (names === undefined || names.length === 0) return null
  return names.join(', ')
}
