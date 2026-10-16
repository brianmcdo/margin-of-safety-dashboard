export type MethodEstimate = {
  method: string
  intrinsic_value_per_share: number | null
  margin_of_safety: number | null
  note: string
}

export type SignalRecord = {
  ticker: string
  name: string | null
  sector: string | null
  industry: string | null
  signal: string
  price: number | null
  graham_number: number | null
  graham_formula: number | null
  dcf_value: number | null
  dcf_low: number | null
  dcf_high: number | null
  ncav_per_share: number | null
  ncav_strict_per_share: number | null
  margin_of_safety: number | null
  composite_score: number
  quality_score: number | null
  f_score: number | null
  f_score_available: number
  altman_z: number | null
  bankruptcy_risk: string
  roic: number | null
  roe: number | null
  low_returns: boolean
  negative_news: boolean
  news_headlines: string[]
  reasons: string[]
  screen_failures: string[]
  is_commodity_producer: boolean
  commodity_unit: string | null
  low_cost_producer: boolean
  methods: MethodEstimate[]
  metrics: Record<string, number | null>
}

export type AppContext = {
  aaa_yield: number
  graham_growth_percent: number
  max_graham_growth_percent: number
  max_graham_implied_pe: number
  discount_receivables: number
  discount_inventory: number
  normalize_years: number
  growth_rate: number
  years: number
  discount_rate: number
  terminal_growth: number
  max_terminal_share: number
  growth_spread: number
  discount_spread: number
  weight_margin: number
  weight_cheapness: number
  weight_quality: number
  quality_minimum: number
  altman_distress: number
  low_return_threshold: number
  low_cost_min_quality: number
  low_cost_max_debt_equity: number
  mos_minimum: number
  portfolio: {
    max_sector_weight: number
    max_position_size: number
    min_holdings: number
  }
  oil: number
  gold: number
  copper: number
  benchmark: string
  assumption: string
}

export type Snapshot = {
  run: {
    run_id: string
    created_at: string
    universe: string
    aaa_yield: number
    failures: number
  } | null
  records: SignalRecord[]
  context: AppContext
}

export type Basket = {
  invested: number
  notes: string[]
  sectors: Record<string, number>
  holdings: {
    ticker: string
    name: string | null
    sector: string
    signal: string
    composite_score: number
    weight: number
  }[]
}

export type CommodityGrid = {
  producer: boolean
  nav: number | null
  note: string
  unit: string
  cells: {
    price_multiplier: number
    discount_rate: number
    value_per_share: number | null
    commodity_price: number | null
  }[]
}

export type HistorySpan = { first: string | null; last: string | null }

export type HistoryResult = {
  as_of: string
  summary: {
    universe_size: number
    selected_count: number
    selected_6m: number | null
    selected_1y: number | null
    universe_6m: number | null
    universe_1y: number | null
    excess_6m: number | null
    excess_1y: number | null
    benchmark_ticker: string
    benchmark_6m: number | null
    benchmark_1y: number | null
    notes: string[]
  }
  selected: {
    ticker: string
    name: string | null
    signal: string
    composite_score: number
    margin_of_safety: number | null
    entry_price: number
    return_6m: number | null
    return_1y: number | null
  }[]
}
