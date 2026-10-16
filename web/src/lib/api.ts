import type { Basket, CommodityGrid, HistoryResult, HistorySpan, Snapshot } from "@/lib/types"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* The body was not JSON. */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export function loadSnapshot() {
  return request<Snapshot>("/api/snapshot")
}

export function runScan(body: { universe: string; limit: number; refresh: boolean }) {
  return request<{ run_id: string; valued: number; failures: number }>("/api/scan", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function buildBasket(body: {
  tickers: string[]
  max_sector_weight: number
  max_position_size: number
  min_holdings: number
}) {
  return request<Basket>("/api/portfolio", { method: "POST", body: JSON.stringify(body) })
}

export function loadCommodity(body: { ticker: string; oil: number; gold: number; copper: number }) {
  return request<CommodityGrid>("/api/commodity", { method: "POST", body: JSON.stringify(body) })
}

export function loadHistorySpan() {
  return request<HistorySpan>("/api/history/span")
}

export function runHistory(body: {
  as_of: string
  min_mos: number
  min_quality: number
  signal: string | null
  benchmark: string
  fetch_prices: boolean
}) {
  return request<HistoryResult>("/api/history", { method: "POST", body: JSON.stringify(body) })
}
