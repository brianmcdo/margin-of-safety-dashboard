import type { SignalRecord } from "@/lib/types"

export type Filters = {
  minMos: number
  minQuality: number
  minCapBillions: number
  sectors: string[]
  signals: string[]
  commodityOnly: boolean
  lowCostOnly: boolean
  query: string
}

const HIDDEN = new Set(["Avoid", "No Signal"])

export function defaultSignals(records: SignalRecord[]) {
  const names = [...new Set(records.map((record) => record.signal))].sort()
  const actionable = names.filter((name) => !HIDDEN.has(name))
  return actionable.length > 0 ? actionable : names
}

export function sectorNames(records: SignalRecord[]) {
  return [...new Set(records.map((record) => record.sector || "Unknown"))].sort()
}

export function applyFilters(records: SignalRecord[], filters: Filters) {
  const needle = filters.query.trim().toLowerCase()
  const minCap = filters.minCapBillions * 1_000_000_000
  return records
    .filter((record) => {
      if (filters.signals.length > 0 && !filters.signals.includes(record.signal)) return false
      const sector = record.sector || "Unknown"
      if (filters.sectors.length > 0 && !filters.sectors.includes(sector)) return false
      if (filters.minMos > 0) {
        const floor = filters.minMos / 100
        const deep = record.signal === "Deep Value / Net-Net" && record.margin_of_safety == null
        if (!deep && (record.margin_of_safety == null || record.margin_of_safety < floor)) {
          return false
        }
      }
      if (filters.minQuality > 0 && record.quality_score != null && record.quality_score < filters.minQuality) {
        return false
      }
      if (minCap > 0 && (record.metrics.market_cap ?? 0) < minCap) return false
      if (filters.commodityOnly && !record.is_commodity_producer) return false
      if (filters.lowCostOnly && !record.low_cost_producer) return false
      if (needle) {
        const name = (record.name ?? "").toLowerCase()
        if (!record.ticker.toLowerCase().includes(needle) && !name.includes(needle)) return false
      }
      return true
    })
    .sort((left, right) => {
      const score = right.composite_score - left.composite_score
      if (score !== 0) return score
      const quality = (right.quality_score ?? -1) - (left.quality_score ?? -1)
      if (quality !== 0) return quality
      return (right.margin_of_safety ?? -1) - (left.margin_of_safety ?? -1)
    })
}

export function counts(records: SignalRecord[]) {
  const tally = new Map<string, number>()
  for (const record of records) tally.set(record.signal, (tally.get(record.signal) ?? 0) + 1)
  return {
    Strong: tally.get("Strong Value") ?? 0,
    Moderate: tally.get("Moderate Value") ?? 0,
    "Net-net": tally.get("Deep Value / Net-Net") ?? 0,
    Watchlist: tally.get("Watchlist") ?? 0,
    Avoid: (tally.get("Avoid") ?? 0) + (tally.get("No Signal") ?? 0),
  }
}
