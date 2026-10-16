export function money(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—"
  return value.toLocaleString("en-US", { style: "currency", currency: "USD" })
}

export function percent(value: number | null | undefined, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—"
  return `${(value * 100).toFixed(digits)}%`
}

export function number(value: number | null | undefined, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—"
  return value.toFixed(digits)
}

export function dcfRange(low: number | null, base: number | null, high: number | null) {
  if (low != null && high != null) return `${money(low)} – ${money(high)}`
  if (base != null) return money(base)
  return "—"
}

export function downloadCsv(filename: string, rows: Record<string, string | number>[]) {
  if (rows.length === 0) return
  const headers = Object.keys(rows[0])
  const lines = [
    headers.join(","),
    ...rows.map((row) =>
      headers
        .map((header) => {
          const cell = String(row[header] ?? "")
          return /[",\n]/.test(cell) ? `"${cell.replaceAll('"', '""')}"` : cell
        })
        .join(",")
    ),
  ]
  const blob = new Blob([lines.join("\n")], { type: "text/csv" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
