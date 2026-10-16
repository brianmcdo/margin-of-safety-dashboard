import { useEffect, useState } from "react"

import { loadHistorySpan, loadSnapshot, runHistory } from "@/lib/api"
import { downloadCsv, money, percent } from "@/lib/format"
import type { HistoryResult } from "@/lib/types"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const SIGNALS = ["default", "strong", "moderate", "deep", "watchlist", "avoid", "all"]

export function HistoricalPage() {
  const [span, setSpan] = useState<{ first: string; last: string } | null>(null)
  const [missing, setMissing] = useState(false)
  const [asOf, setAsOf] = useState("")
  const [minMos, setMinMos] = useState(30)
  const [minQuality, setMinQuality] = useState(40)
  const [signal, setSignal] = useState("default")
  const [benchmark, setBenchmark] = useState("SPY")
  const [fetchPrices, setFetchPrices] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<HistoryResult | null>(null)

  useEffect(() => {
    void (async () => {
      const [dates, snapshot] = await Promise.all([loadHistorySpan(), loadSnapshot()])
      setMinMos(Math.round(snapshot.context.mos_minimum * 100))
      setMinQuality(Math.round(snapshot.context.quality_minimum))
      setBenchmark(snapshot.context.benchmark || "SPY")
      if (!dates.first || !dates.last) {
        setMissing(true)
        return
      }
      setSpan({ first: dates.first, last: dates.last })
      setAsOf(dates.last)
    })().catch((exc: unknown) => {
      setError(exc instanceof Error ? exc.message : "Could not read stored snapshots")
    })
  }, [])

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const next = await runHistory({
        as_of: asOf,
        min_mos: minMos / 100,
        min_quality: minQuality,
        signal: signal === "default" ? null : signal,
        benchmark,
        fetch_prices: fetchPrices,
      })
      setResult(next)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The historical screen failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Historical screen</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Replay the screener on fundamentals and closes stored on or before a date. The result is the
          equal-weight price return of the names that cleared the filters, next to the equal-weight return
          of every name valued that day. Dividends, costs, and delistings are left out. This is not a
          portfolio and not investment advice.
        </p>
      </div>
      {missing ? (
        <Alert>
          <AlertTitle>No point-in-time snapshot is stored yet</AlertTitle>
          <AlertDescription>Run a scan first. Each scan keeps the day it was fetched.</AlertDescription>
        </Alert>
      ) : null}
      {span ? (
        <p className="text-xs text-muted-foreground">
          Stored snapshots run from {span.first} to {span.last}.
        </p>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>As of</CardTitle>
          <CardDescription>Choose a date and run the screen. Opening this page does not fetch market data.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="as-of">Date</Label>
            <Input
              id="as-of"
              type="date"
              value={asOf}
              min={span?.first}
              max={span ? later(span.last) : undefined}
              onChange={(event) => setAsOf(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label>Signal</Label>
            <Select value={signal} onValueChange={(value) => setSignal(value ?? "default")}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SIGNALS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item === "default" ? "Default (strong, moderate, deep)" : item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <div className="flex justify-between text-sm">
              <Label>Minimum margin of safety</Label>
              <span className="text-muted-foreground">{minMos}%</span>
            </div>
            <Slider min={0} max={70} step={5} value={[minMos]} onValueChange={(value) => setMinMos(value[0] ?? 0)} />
          </div>
          <div className="grid gap-2">
            <div className="flex justify-between text-sm">
              <Label>Minimum quality score</Label>
              <span className="text-muted-foreground">{minQuality}</span>
            </div>
            <Slider min={0} max={100} step={5} value={[minQuality]} onValueChange={(value) => setMinQuality(value[0] ?? 0)} />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="benchmark">Benchmark ticker</Label>
            <Input id="benchmark" value={benchmark} onChange={(event) => setBenchmark(event.target.value)} />
          </div>
          <label className="flex items-end gap-2 text-sm">
            <Checkbox checked={fetchPrices} onCheckedChange={(checked) => setFetchPrices(checked === true)} />
            Download missing closes from Yahoo
          </label>
          <div className="md:col-span-2">
            <Button onClick={() => void run()} disabled={busy || !asOf}>
              {busy ? "Running…" : "Run historical screen"}
            </Button>
          </div>
        </CardContent>
      </Card>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {result ? <HistoryResultView result={result} /> : null}
    </div>
  )
}

function later(day: string) {
  const today = new Date().toISOString().slice(0, 10)
  return today > day ? today : day
}

function HistoryResultView({ result }: { result: HistoryResult }) {
  const summary = result.summary
  const metrics = [
    ["Valued", String(summary.universe_size)],
    ["Selected", String(summary.selected_count)],
    ["Selected 6-month", percent(summary.selected_6m)],
    ["Selected 1-year", percent(summary.selected_1y)],
    ["Universe 6-month", percent(summary.universe_6m)],
    ["Excess 6-month", percent(summary.excess_6m)],
    ["Universe 1-year", percent(summary.universe_1y)],
    ["Excess 1-year", percent(summary.excess_1y)],
    [`${summary.benchmark_ticker} 6-month`, percent(summary.benchmark_6m)],
    [`${summary.benchmark_ticker} 1-year`, percent(summary.benchmark_1y)],
  ]
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {metrics.map(([label, value]) => (
          <Card key={label} size="sm">
            <CardHeader>
              <CardDescription>{label}</CardDescription>
              <CardTitle>{value}</CardTitle>
            </CardHeader>
          </Card>
        ))}
      </div>
      {summary.notes.map((note) => (
        <p key={note} className="text-xs text-muted-foreground">
          {note}
        </p>
      ))}
      {result.selected.length === 0 ? (
        <Alert>
          <AlertDescription>No names cleared these filters on the stored snapshots.</AlertDescription>
        </Alert>
      ) : (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Selected names</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                downloadCsv(
                  `backtest_${result.as_of}.csv`,
                  result.selected.map((row) => ({
                    Ticker: row.ticker,
                    Name: row.name ?? "",
                    Signal: row.signal,
                    Score: row.composite_score.toFixed(1),
                    "MoS %": row.margin_of_safety == null ? "" : (row.margin_of_safety * 100).toFixed(1),
                    Entry: row.entry_price.toFixed(2),
                    "6-month %": row.return_6m == null ? "—" : percent(row.return_6m),
                    "1-year %": row.return_1y == null ? "—" : percent(row.return_1y),
                  }))
                )
              }
            >
              Download CSV
            </Button>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  {["Ticker", "Name", "Signal", "Score", "MoS %", "Entry", "6-month %", "1-year %"].map((column) => (
                    <TableHead key={column}>{column}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {result.selected.map((row) => (
                  <TableRow key={row.ticker}>
                    <TableCell className="font-medium">{row.ticker}</TableCell>
                    <TableCell>{row.name}</TableCell>
                    <TableCell>{row.signal}</TableCell>
                    <TableCell>{row.composite_score.toFixed(1)}</TableCell>
                    <TableCell>{row.margin_of_safety == null ? "—" : (row.margin_of_safety * 100).toFixed(1)}</TableCell>
                    <TableCell>{money(row.entry_price)}</TableCell>
                    <TableCell>{row.return_6m == null ? "—" : percent(row.return_6m)}</TableCell>
                    <TableCell>{row.return_1y == null ? "—" : percent(row.return_1y)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
