import { useEffect, useMemo, useState } from "react"

import { buildBasket, loadCommodity, loadSnapshot, runScan } from "@/lib/api"
import { applyFilters, counts, defaultSignals, sectorNames, type Filters } from "@/lib/filter"
import { dcfRange, downloadCsv, money, number, percent } from "@/lib/format"
import type { AppContext, Basket, CommodityGrid, SignalRecord, Snapshot } from "@/lib/types"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Slider } from "@/components/ui/slider"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const MOS_EDGES = [-1.5, -1, -0.5, -0.25, 0, 0.15, 0.25, 0.4, 0.6, 1]
const SCORE_EDGES = [0, 20, 40, 50, 60, 70, 80, 90, 100]

export function ResearchPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState<Filters | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    setError(null)
    try {
      const next = await loadSnapshot()
      setSnapshot(next)
      setFilters((current) => current ?? initialFilters(next))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not load the snapshot")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const ranked = useMemo(
    () => (snapshot && filters ? applyFilters(snapshot.records, filters) : []),
    [snapshot, filters]
  )

  useEffect(() => {
    if (ranked.length === 0) {
      setSelected(null)
      return
    }
    if (!selected || !ranked.some((record) => record.ticker === selected)) {
      setSelected(ranked[0].ticker)
    }
  }, [ranked, selected])

  if (loading && !snapshot) {
    return <p className="text-sm text-muted-foreground">Loading the saved snapshot…</p>
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>The snapshot did not load</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    )
  }
  if (!snapshot || !filters) return null

  const context = snapshot.context
  const tally = counts(snapshot.records)
  const chosen = ranked.find((record) => record.ticker === selected) ?? null

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <FilterColumn
        snapshot={snapshot}
        filters={filters}
        onChange={setFilters}
        onScanned={() => void refresh()}
      />
      <div className="flex min-w-0 flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Margin of Safety</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Equity research signals from Graham, NCAV, and a conservative free-cash-flow model. A high
            score is a candidate for reading, not an instruction to buy. Not investment advice.
          </p>
        </div>
        {snapshot.run ? (
          <p className="text-xs text-muted-foreground">
            Loaded from {snapshot.run.run_id} · universe {snapshot.run.universe} · AAA yield{" "}
            {snapshot.run.aaa_yield.toFixed(2)}% · {snapshot.run.failures} fetch failures. Opening this
            page does not request new market data.
          </p>
        ) : (
          <Alert>
            <AlertTitle>No snapshot yet</AlertTitle>
            <AlertDescription>
              Run a sample scan from the filters. The first pass needs the network.
            </AlertDescription>
          </Alert>
        )}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {Object.entries(tally).map(([label, value]) => (
            <Card key={label} size="sm">
              <CardHeader>
                <CardDescription>{label}</CardDescription>
                <CardTitle className="text-2xl">{value}</CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>
        <Formulas context={context} />
        <Charts records={ranked} />
        <RankedTable
          records={ranked}
          selected={selected}
          runId={snapshot.run?.run_id ?? "signals"}
          onSelect={setSelected}
        />
        <Basket records={ranked} context={context} />
        {chosen ? <StockDetail record={chosen} context={context} /> : null}
      </div>
    </div>
  )
}

function initialFilters(snapshot: Snapshot): Filters {
  return {
    minMos: Math.round(snapshot.context.mos_minimum * 100),
    minQuality: Math.round(snapshot.context.quality_minimum),
    minCapBillions: 0,
    sectors: sectorNames(snapshot.records),
    signals: defaultSignals(snapshot.records),
    commodityOnly: false,
    lowCostOnly: false,
    query: "",
  }
}

function FilterColumn({
  snapshot,
  filters,
  onChange,
  onScanned,
}: {
  snapshot: Snapshot
  filters: Filters
  onChange: (filters: Filters) => void
  onScanned: () => void
}) {
  const sectors = sectorNames(snapshot.records)
  const signals = [...new Set(snapshot.records.map((record) => record.signal))].sort()
  const patch = (partial: Partial<Filters>) => onChange({ ...filters, ...partial })

  function toggle(list: string[], value: string, key: "sectors" | "signals") {
    const next = list.includes(value) ? list.filter((item) => item !== value) : [...list, value]
    patch({ [key]: next })
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>These apply to the table, the charts, and the CSV.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <SliderField
            label="Minimum margin of safety"
            value={filters.minMos}
            max={70}
            step={5}
            suffix="%"
            onChange={(minMos) => patch({ minMos })}
          />
          <SliderField
            label="Minimum quality score"
            value={filters.minQuality}
            max={100}
            step={5}
            onChange={(minQuality) => patch({ minQuality })}
          />
          <SliderField
            label="Minimum market cap"
            value={filters.minCapBillions}
            max={200}
            step={10}
            suffix=" $B"
            onChange={(minCapBillions) => patch({ minCapBillions })}
          />
          <CheckList
            title="Sector"
            options={sectors}
            selected={filters.sectors}
            onToggle={(value) => toggle(filters.sectors, value, "sectors")}
          />
          <CheckList
            title="Signal"
            options={signals}
            selected={filters.signals}
            onToggle={(value) => toggle(filters.signals, value, "signals")}
          />
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={filters.commodityOnly}
              onCheckedChange={(checked) => patch({ commodityOnly: checked === true })}
            />
            Commodity producers only
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={filters.lowCostOnly}
              onCheckedChange={(checked) => patch({ lowCostOnly: checked === true })}
            />
            Low-cost producers only
          </label>
          <div className="grid gap-2">
            <Label htmlFor="query">Ticker or name</Label>
            <Input
              id="query"
              value={filters.query}
              placeholder="Exxon"
              onChange={(event) => patch({ query: event.target.value })}
            />
          </div>
        </CardContent>
      </Card>
      <ScanCard onScanned={onScanned} />
    </div>
  )
}

function SliderField({
  label,
  value,
  max,
  step,
  suffix = "",
  onChange,
}: {
  label: string
  value: number
  max: number
  step: number
  suffix?: string
  onChange: (value: number) => void
}) {
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between text-sm">
        <Label>{label}</Label>
        <span className="text-muted-foreground">
          {value}
          {suffix}
        </span>
      </div>
      <Slider
        min={0}
        max={max}
        step={step}
        value={[value]}
        onValueChange={(next) => onChange(next[0] ?? 0)}
      />
    </div>
  )
}

function CheckList({
  title,
  options,
  selected,
  onToggle,
}: {
  title: string
  options: string[]
  selected: string[]
  onToggle: (value: string) => void
}) {
  if (options.length === 0) return null
  return (
    <div className="grid gap-2">
      <Label>{title}</Label>
      <ScrollArea className="h-36 rounded-md border">
        <div className="flex flex-col gap-2 p-2">
          {options.map((option) => (
            <label key={option} className="flex items-center gap-2 text-sm">
              <Checkbox checked={selected.includes(option)} onCheckedChange={() => onToggle(option)} />
              {option}
            </label>
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}

function ScanCard({ onScanned }: { onScanned: () => void }) {
  const [universe, setUniverse] = useState("sample")
  const [tickers, setTickers] = useState("")
  const [limit, setLimit] = useState("0")
  const [refreshCache, setRefreshCache] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function scan() {
    setBusy(true)
    setMessage(null)
    try {
      const result = await runScan({
        universe: tickers.trim() || universe,
        limit: Number(limit) || 0,
        refresh: refreshCache,
      })
      setMessage(`Stored ${result.run_id}. Valued ${result.valued}.`)
      onScanned()
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : "Scan failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Run a scan</CardTitle>
        <CardDescription>The page loads the last saved snapshot until you run this.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid gap-2">
          <Label htmlFor="universe">Universe</Label>
          <Input id="universe" value={universe} onChange={(event) => setUniverse(event.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="tickers">Or tickers</Label>
          <Input
            id="tickers"
            placeholder="INTC, XOM, NEM"
            value={tickers}
            onChange={(event) => setTickers(event.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="limit">Limit (0 = all)</Label>
          <Input id="limit" type="number" min={0} value={limit} onChange={(event) => setLimit(event.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={refreshCache} onCheckedChange={(checked) => setRefreshCache(checked === true)} />
          Ignore the 24-hour cache
        </label>
        <Button onClick={() => void scan()} disabled={busy}>
          {busy ? "Scanning…" : "Run scan"}
        </Button>
        {message ? <p className="text-xs text-muted-foreground">{message}</p> : null}
      </CardContent>
    </Card>
  )
}

function Formulas({ context }: { context: AppContext }) {
  const items = [
    ["Margin of safety", "(Intrinsic value − Price) / Intrinsic value", "The headline figure is the median across methods that return a value."],
    ["Graham Number", "√(22.5 × EPS × Book value per share)", "22.5 is 15 × 1.5. Both inputs must be positive, or the method abstains."],
    [
      "Graham formula",
      "V = EPS × (8.5 + 2g) × 4.4 / Y",
      `g is ${context.graham_growth_percent}% growth, capped at ${context.max_graham_growth_percent}%. Y is the AAA yield, ${context.aaa_yield.toFixed(2)}% here. The implied P/E is capped at ${context.max_graham_implied_pe}.`,
    ],
    [
      "NCAV per share",
      "(Current assets − Total liabilities) / Shares",
      `Strict version: cash + ${Math.round(context.discount_receivables * 100)}% of receivables + ${Math.round(context.discount_inventory * 100)}% of inventory + other current assets, minus total liabilities.`,
    ],
    [
      "Conservative DCF",
      "Value = PV(explicit cash flows + capped terminal value) / Shares",
      `Median free cash flow over ${context.normalize_years} years, grown at ${percent(context.growth_rate)} for ${context.years} years, discounted at ${percent(context.discount_rate)}. Terminal growth ${percent(context.terminal_growth)}, capped at ${percent(context.max_terminal_share, 0)} of the total.`,
    ],
    [
      "Score",
      "100 × weighted average of margin of safety, relative cheapness, and quality",
      `Weights ${context.weight_margin}, ${context.weight_cheapness}, and ${context.weight_quality}. Cheapness is the sector percentile, or the industry when the sector has fewer than five names.`,
    ],
  ] as const
  return (
    <Card>
      <CardHeader>
        <CardTitle>Formulas</CardTitle>
        <CardDescription>The identities used for this snapshot. Commodity NAV stays out of the score.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        {items.map(([title, formula, note]) => (
          <div key={title}>
            <p className="text-sm font-medium">{title}</p>
            <p className="font-mono text-xs text-primary">{formula}</p>
            <p className="text-xs text-muted-foreground">{note}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function Charts({ records }: { records: SignalRecord[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Margin of safety</CardTitle>
        </CardHeader>
        <CardContent>
          <Bars values={records.map((record) => record.margin_of_safety)} edges={MOS_EDGES} asPercent />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Composite score</CardTitle>
        </CardHeader>
        <CardContent>
          <Bars values={records.map((record) => record.composite_score)} edges={SCORE_EDGES} />
        </CardContent>
      </Card>
    </div>
  )
}

function Bars({
  values,
  edges,
  asPercent = false,
}: {
  values: (number | null)[]
  edges: number[]
  asPercent?: boolean
}) {
  const usable = values.filter((value): value is number => value != null)
  if (usable.length === 0) {
    return <p className="text-xs text-muted-foreground">Nothing in this filter has a value.</p>
  }
  const counts = edges.slice(0, -1).map(() => 0)
  for (const value of usable) {
    const index = edges.findIndex((edge, position) => position < edges.length - 1 && value >= edge && value < edges[position + 1])
    const bin = index === -1 ? counts.length - 1 : index
    counts[bin] += 1
  }
  const peak = Math.max(...counts, 1)
  return (
    <div className="flex h-40 items-end gap-1">
      {counts.map((count, index) => {
        const start = asPercent ? `${Math.round(edges[index] * 100)}%` : `${edges[index]}`
        return (
          <div key={start} className="flex h-full flex-1 flex-col justify-end gap-1">
            <div
              className="rounded-sm bg-primary"
              style={{ height: `${(count / peak) * 100}%` }}
              title={`${start}: ${count}`}
            />
            <span className="truncate text-center text-[10px] text-muted-foreground">{start}</span>
          </div>
        )
      })}
    </div>
  )
}

function signalVariant(signal: string): "default" | "secondary" | "outline" | "destructive" {
  if (signal === "Strong Value") return "default"
  if (signal === "Avoid" || signal === "No Signal") return "destructive"
  if (signal === "Deep Value / Net-Net") return "outline"
  return "secondary"
}

function RankedTable({
  records,
  selected,
  runId,
  onSelect,
}: {
  records: SignalRecord[]
  selected: string | null
  runId: string
  onSelect: (ticker: string) => void
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Ranked names · {records.length}</CardTitle>
        <Button
          variant="outline"
          size="sm"
          disabled={records.length === 0}
          onClick={() =>
            downloadCsv(`signals_${runId}.csv`, records.map((record) => ({
              Ticker: record.ticker,
              Price: record.price ?? "",
              "MoS %": record.margin_of_safety == null ? "" : (record.margin_of_safety * 100).toFixed(1),
              "Graham Number": record.graham_number ?? "",
              "DCF range": dcfRange(record.dcf_low, record.dcf_value, record.dcf_high),
              Quality: record.quality_score ?? "",
              Signal: record.signal,
              "Low-cost": record.low_cost_producer ? "Yes" : "",
              News: record.negative_news ? "Negative" : "",
              Score: record.composite_score.toFixed(1),
            })))
          }
        >
          Download CSV
        </Button>
      </CardHeader>
      <CardContent>
        {records.length === 0 ? (
          <Alert>
            <AlertTitle>Nothing matches these filters</AlertTitle>
            <AlertDescription>Widen a slider or clear a signal checkbox.</AlertDescription>
          </Alert>
        ) : (
          <div className="max-h-[28rem] overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {["Ticker", "Price", "MoS %", "Graham", "DCF range", "Quality", "Signal", "Low-cost", "News", "Score"].map((column) => (
                    <TableHead key={column}>{column}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map((record) => (
                  <TableRow
                    key={record.ticker}
                    data-state={record.ticker === selected ? "selected" : undefined}
                    className="cursor-pointer"
                    onClick={() => onSelect(record.ticker)}
                  >
                    <TableCell className="font-medium">{record.ticker}</TableCell>
                    <TableCell>{money(record.price)}</TableCell>
                    <TableCell>{record.margin_of_safety == null ? "—" : (record.margin_of_safety * 100).toFixed(1)}</TableCell>
                    <TableCell>{money(record.graham_number)}</TableCell>
                    <TableCell>{dcfRange(record.dcf_low, record.dcf_value, record.dcf_high)}</TableCell>
                    <TableCell>{number(record.quality_score, 0)}</TableCell>
                    <TableCell>
                      <Badge variant={signalVariant(record.signal)}>{record.signal}</Badge>
                    </TableCell>
                    <TableCell>{record.low_cost_producer ? "Yes" : ""}</TableCell>
                    <TableCell>{record.negative_news ? "Negative" : ""}</TableCell>
                    <TableCell>{record.composite_score.toFixed(1)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Basket({ records, context }: { records: SignalRecord[]; context: AppContext }) {
  const [sector, setSector] = useState(Math.round(context.portfolio.max_sector_weight * 100))
  const [position, setPosition] = useState(Math.round(context.portfolio.max_position_size * 100))
  const [holdings, setHoldings] = useState(String(context.portfolio.min_holdings))
  const [basket, setBasket] = useState<Basket | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = records.map((record) => record.ticker).join(",")

  useEffect(() => {
    let cancel = false
    buildBasket({
      tickers: records.map((record) => record.ticker),
      max_sector_weight: sector / 100,
      max_position_size: position / 100,
      min_holdings: Math.max(1, Number(holdings) || 1),
    })
      .then((next) => {
        if (!cancel) {
          setBasket(next)
          setError(null)
        }
      })
      .catch((exc: unknown) => {
        if (!cancel) setError(exc instanceof Error ? exc.message : "Basket failed")
      })
    return () => {
      cancel = true
    }
  }, [key, sector, position, holdings, records])

  const mix = basket
    ? Object.entries(basket.sectors)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([name, weight]) => `${name} ${percent(weight, 0)}`)
        .join(" · ")
    : ""

  return (
    <Card>
      <CardHeader>
        <CardTitle>Suggested basket</CardTitle>
        <CardDescription>
          Takes names in rank order until the next one would break a cap. Weight that cannot be placed is left unallocated. Not investment advice.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-4 md:grid-cols-3">
          <SliderField label="Max sector weight" value={sector} max={100} step={5} suffix="%" onChange={setSector} />
          <SliderField label="Max position size" value={position} max={100} step={1} suffix="%" onChange={setPosition} />
          <div className="grid gap-2">
            <Label htmlFor="min-holdings">Minimum holdings</Label>
            <Input id="min-holdings" type="number" min={1} max={40} value={holdings} onChange={(event) => setHoldings(event.target.value)} />
          </div>
        </div>
        {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
        {basket && basket.holdings.length > 0 ? (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  {["Ticker", "Name", "Sector", "Signal", "Score", "Weight %"].map((column) => (
                    <TableHead key={column}>{column}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {basket.holdings.map((holding) => (
                  <TableRow key={holding.ticker}>
                    <TableCell className="font-medium">{holding.ticker}</TableCell>
                    <TableCell>{holding.name}</TableCell>
                    <TableCell>{holding.sector}</TableCell>
                    <TableCell>{holding.signal}</TableCell>
                    <TableCell>{holding.composite_score.toFixed(1)}</TableCell>
                    <TableCell>{(holding.weight * 100).toFixed(1)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-xs text-muted-foreground">
              Invested {percent(basket.invested, 0)}. {mix}
            </p>
            {basket.notes.map((note) => (
              <Alert key={note}>
                <AlertDescription>{note}</AlertDescription>
              </Alert>
            ))}
            <Button
              variant="outline"
              className="w-fit"
              onClick={() =>
                downloadCsv(
                  "suggested_basket.csv",
                  basket.holdings.map((holding) => ({
                    Ticker: holding.ticker,
                    Name: holding.name ?? "",
                    Sector: holding.sector,
                    Signal: holding.signal,
                    Score: holding.composite_score.toFixed(1),
                    "Weight %": (holding.weight * 100).toFixed(1),
                  }))
                )
              }
            >
              Download basket CSV
            </Button>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">No names fit these caps.</p>
        )}
      </CardContent>
    </Card>
  )
}

function StockDetail({
  record,
  context,
}: {
  record: SignalRecord
  context: AppContext
}) {
  const [oil, setOil] = useState(String(context.oil))
  const [gold, setGold] = useState(String(context.gold))
  const [copper, setCopper] = useState(String(context.copper))
  const [grid, setGrid] = useState<CommodityGrid | null>(null)

  useEffect(() => {
    let cancel = false
    loadCommodity({
      ticker: record.ticker,
      oil: Number(oil) || 0,
      gold: Number(gold) || 0,
      copper: Number(copper) || 0,
    })
      .then((next) => {
        if (!cancel) setGrid(next)
      })
      .catch(() => {
        if (!cancel) setGrid(null)
      })
    return () => {
      cancel = true
    }
  }, [record.ticker, oil, gold, copper])

  const returnLabel = record.roic != null ? "ROIC" : "ROE"
  const returnValue = record.roic ?? record.roe
  const comparable = [
    ["Price", record.price],
    ["Graham Number", record.graham_number],
    ["Graham Formula", record.graham_formula],
    ["DCF", record.dcf_value],
    ["NCAV", record.ncav_per_share],
    ["Strict NCAV", record.ncav_strict_per_share],
  ] as const
  const peak = Math.max(...comparable.map(([, value]) => (value != null && value > 0 ? value : 0)), 1)

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {record.ticker} · {record.name || record.ticker}
        </CardTitle>
        <CardDescription>
          {record.sector || "Unknown sector"} · {record.industry || "Industry not reported"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {record.low_cost_producer ? (
          <Alert>
            <AlertTitle>Low-cost producer</AlertTitle>
            <AlertDescription>
              Quality, positive free cash flow, and modest debt clear the configured floors. This is not an all-in sustaining cost.
            </AlertDescription>
          </Alert>
        ) : null}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Metric label="Price" value={money(record.price)} />
          <Metric label="Median margin of safety" value={percent(record.margin_of_safety)} />
          <Metric label="Score" value={record.composite_score.toFixed(1)} />
          <Metric label="Signal" value={record.signal} />
          <Metric label="Quality" value={number(record.quality_score, 0)} />
          <Metric
            label="F-score"
            value={record.f_score_available > 0 ? `${record.f_score}/${record.f_score_available}` : "—"}
          />
          <Metric label="Altman Z" value={number(record.altman_z, 2)} />
          <Metric label={returnLabel} value={percent(returnValue)} />
        </div>
        {record.bankruptcy_risk === "distress" || record.low_returns ? (
          <Alert variant="destructive">
            <AlertDescription>
              Quality flag:{" "}
              {[
                record.bankruptcy_risk === "distress" ? "bankruptcy risk is in the distress zone" : "",
                record.low_returns ? "returns have been consistently low" : "",
              ]
                .filter(Boolean)
                .join("; ")}
              .
            </AlertDescription>
          </Alert>
        ) : null}
        {record.negative_news ? (
          <Alert variant="destructive">
            <AlertDescription>
              Very negative recent headlines
              {record.news_headlines.length > 0
                ? `: ${record.news_headlines.slice(0, 4).map((title) => `"${title}"`).join("; ")}`
                : ""}
              . This is a keyword check on Yahoo titles, not a filing review.
            </AlertDescription>
          </Alert>
        ) : null}
        <div>
          <p className="mb-1 text-sm font-medium">Why it was flagged</p>
          {record.reasons.length > 0 ? (
            <ul className="list-disc pl-5 text-sm text-muted-foreground">
              {record.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">This snapshot has no stored reasons for this name.</p>
          )}
        </div>
        {record.screen_failures.length > 0 ? (
          <Alert>
            <AlertDescription>Screen: {record.screen_failures.join("; ")}</AlertDescription>
          </Alert>
        ) : null}
        <div>
          <p className="mb-2 text-sm font-medium">Valuation methods</p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Method</TableHead>
                <TableHead>Value / share</TableHead>
                <TableHead>Margin of safety</TableHead>
                <TableHead>Note</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {record.methods.map((method) => (
                <TableRow key={method.method}>
                  <TableCell>{method.method}</TableCell>
                  <TableCell>{money(method.intrinsic_value_per_share)}</TableCell>
                  <TableCell>{percent(method.margin_of_safety)}</TableCell>
                  <TableCell className="max-w-sm whitespace-normal text-muted-foreground">{method.note}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <p className="mt-2 text-xs text-muted-foreground">
            {record.dcf_low != null || record.dcf_high != null
              ? `DCF range: low ${money(record.dcf_low)} · base ${money(record.dcf_value)} · high ${money(record.dcf_high)}`
              : record.dcf_value != null
                ? `DCF base case ${money(record.dcf_value)}. This snapshot has no low/high band.`
                : "No DCF value is stored."}
          </p>
          <div className="mt-3 flex flex-col gap-2">
            {comparable.map(([label, value]) => (
              <div key={label} className="grid grid-cols-[8rem_1fr_5rem] items-center gap-2 text-xs">
                <span>{label}</span>
                <div className="h-2 rounded-full bg-muted">
                  <div
                    className="h-2 rounded-full bg-primary"
                    style={{ width: `${value != null && value > 0 ? (value / peak) * 100 : 0}%` }}
                  />
                </div>
                <span className="text-right">{money(value)}</span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 text-sm font-medium">Commodity sensitivity</p>
          <div className="mb-3 grid gap-3 md:grid-cols-3">
            <NumberField id="oil" label="Oil planning price ($/bbl)" value={oil} onChange={setOil} />
            <NumberField id="gold" label="Gold planning price ($/oz)" value={gold} onChange={setGold} />
            <NumberField id="copper" label="Copper planning price ($/lb)" value={copper} onChange={setCopper} />
          </div>
          {grid?.producer && grid.nav != null ? <Metric label="Commodity NAV / share" value={money(grid.nav)} /> : null}
          <p className="mt-2 text-xs text-muted-foreground">{context.assumption}</p>
          {grid?.note ? <p className="text-xs text-muted-foreground">{grid.note}</p> : null}
          {grid && grid.cells.length > 0 ? <Sensitivity cells={grid.cells} unit={grid.unit} /> : (
            <p className="mt-2 text-xs text-muted-foreground">
              No commodity-price sensitivity table for this name. That grid is built for energy and mining companies.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function Sensitivity({ cells, unit }: { cells: CommodityGrid["cells"]; unit: string }) {
  const discounts = [...new Set(cells.map((cell) => cell.discount_rate))].sort((a, b) => a - b)
  const priced = cells.some((cell) => cell.commodity_price != null)
  const rows = [...new Set(cells.map((cell) => (priced ? cell.commodity_price : cell.price_multiplier)))].sort(
    (a, b) => (a ?? 0) - (b ?? 0)
  )
  return (
    <div className="mt-3 overflow-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Commodity price</TableHead>
            {discounts.map((discount) => (
              <TableHead key={discount}>{percent(discount, 0)} discount</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={String(row)}>
              <TableCell>
                {priced ? `${(row ?? 0).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${unit}`.trim() : percent(row, 0) + " of base cash flow"}
              </TableCell>
              {discounts.map((discount) => {
                const cell = cells.find((item) => {
                  const key = priced ? item.commodity_price : item.price_multiplier
                  return key === row && item.discount_rate === discount
                })
                return <TableCell key={discount}>{money(cell?.value_per_share)}</TableCell>
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}

function NumberField({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  )
}
