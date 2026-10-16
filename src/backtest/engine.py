"""Replay a stored snapshot and measure simple forward price returns.

The screen uses the latest fundamentals stored on or before the as-of date,
and the latest close stored on or before that date. A later snapshot is not
visible. Returns are price changes only: no dividends, costs, or position sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from src.config import Settings
from src.data.cache import SnapshotStore
from src.data.models import SignalRecord
from src.pipeline import evaluate_stocks
from src.signals.generator import rank_records

SIX_MONTHS = 6
ONE_YEAR = 12
HORIZONS = (SIX_MONTHS, ONE_YEAR)
PRICE_TOLERANCE_DAYS = 10


@dataclass
class NameReturn:
    ticker: str
    name: str | None
    signal: str
    composite_score: float
    margin_of_safety: float | None
    entry_date: date
    entry_price: float
    return_6m: float | None = None
    return_1y: float | None = None


@dataclass
class PerformanceSummary:
    """Equal-weight averages. Missing horizons are left blank."""

    universe_size: int
    selected_count: int
    priced_6m: int
    priced_1y: int
    selected_6m: float | None
    selected_1y: float | None
    universe_6m: float | None
    universe_1y: float | None
    benchmark_ticker: str
    benchmark_6m: float | None
    benchmark_1y: float | None
    notes: list[str] = field(default_factory=list)

    @property
    def excess_6m(self) -> float | None:
        if self.selected_6m is None or self.universe_6m is None:
            return None
        return self.selected_6m - self.universe_6m

    @property
    def excess_1y(self) -> float | None:
        if self.selected_1y is None or self.universe_1y is None:
            return None
        return self.selected_1y - self.universe_1y


@dataclass
class BacktestResult:
    as_of: date
    records: list[SignalRecord]
    selected: list[NameReturn]
    summary: PerformanceSummary


def add_months(day: date, months: int) -> date:
    """Calendar months, clipped to the last day of the destination month."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = _month_length(year, month)
    return date(year, month, min(day.day, last_day))


def price_return(entry: float, exit_price: float) -> float | None:
    """Simple price return. A non-positive entry price has no return."""
    if entry <= 0 or exit_price <= 0:
        return None
    return exit_price / entry - 1.0


def qualifying_exit(
    quote: tuple[date, float] | None,
    *,
    entry_date: date,
    horizon: date,
    tolerance_days: int = PRICE_TOLERANCE_DAYS,
) -> tuple[date, float] | None:
    """Accept a close only when it sits on the horizon, within a few days.

    The quote is the latest close on or before the horizon. A close from the
    entry week is not treated as a six-month or one-year exit.
    """
    if quote is None:
        return None
    price_date, price = quote
    if price_date <= entry_date:
        return None
    if (horizon - price_date).days > tolerance_days:
        return None
    return price_date, price


def run_historical_screen(
    store: SnapshotStore,
    settings: Settings,
    as_of: date,
    *,
    min_mos: float | None = None,
    min_quality: float | None = None,
    signal: str | None = None,
    benchmark: str | None = None,
    limit: int | None = None,
) -> BacktestResult:
    """Screen stored snapshots as of ``as_of`` and attach forward returns.

    The result is not written as a new scan, so the dashboard's latest
    snapshot stays unchanged.
    """
    floor = settings.valuation.mos.minimum if min_mos is None else min_mos
    quality_floor = settings.scoring.quality.minimum if min_quality is None else min_quality
    benchmark_ticker = (benchmark or settings.backtest.benchmark).strip() or "SPY"
    tolerance_days = settings.backtest.price_tolerance_days
    stocks = store.fundamentals_as_of(as_of)
    stocks.sort(key=lambda stock: stock.ticker)
    if limit is not None and limit >= 0:
        stocks = stocks[:limit]

    priced = []
    entry_dates: dict[str, date] = {}
    for stock in stocks:
        quote = store.price_on_or_before(stock.ticker, as_of)
        if quote is None:
            continue
        entry_date, entry_price = quote
        updated = {"price": entry_price}
        if stock.shares_outstanding is not None and stock.shares_outstanding > 0:
            updated["market_cap"] = entry_price * stock.shares_outstanding
        priced.append(stock.model_copy(update=updated))
        entry_dates[stock.ticker] = entry_date

    aaa = store.aaa_yield_as_of(as_of)
    if aaa is None:
        aaa = settings.fred.fallback_aaa_yield
    records, _failures = evaluate_stocks(priced, settings, aaa)
    selected_records = rank_records(records, floor, min_quality=quality_floor, signal=signal)
    selected_ids = {record.ticker for record in selected_records}

    horizons = {months: add_months(as_of, months) for months in HORIZONS}
    universe_returns = {months: [] for months in HORIZONS}
    selected_rows: list[NameReturn] = []
    for record in records:
        entry_date = entry_dates.get(record.ticker)
        entry_price = record.price
        if entry_date is None or entry_price is None:
            continue
        returns = _horizon_returns(
            store, record.ticker, entry_date, entry_price, horizons, tolerance_days
        )
        for months, value in returns.items():
            if value is not None:
                universe_returns[months].append(value)
        if record.ticker not in selected_ids:
            continue
        selected_rows.append(
            NameReturn(
                ticker=record.ticker,
                name=record.name,
                signal=record.signal,
                composite_score=record.composite_score,
                margin_of_safety=record.margin_of_safety,
                entry_date=entry_date,
                entry_price=entry_price,
                return_6m=returns[SIX_MONTHS],
                return_1y=returns[ONE_YEAR],
            )
        )
    rank = {record.ticker: index for index, record in enumerate(selected_records)}
    selected_rows.sort(key=lambda row: rank.get(row.ticker, len(rank)))

    benchmark_entry = _benchmark_entry(store, benchmark_ticker, as_of)
    if benchmark_entry is None:
        benchmark_returns = {months: None for months in HORIZONS}
    else:
        benchmark_returns = _horizon_returns(
            store, benchmark_ticker, as_of, benchmark_entry, horizons, tolerance_days
        )

    summary = PerformanceSummary(
        universe_size=len(records),
        selected_count=len(selected_rows),
        priced_6m=sum(1 for row in selected_rows if row.return_6m is not None),
        priced_1y=sum(1 for row in selected_rows if row.return_1y is not None),
        selected_6m=_mean([row.return_6m for row in selected_rows if row.return_6m is not None]),
        selected_1y=_mean([row.return_1y for row in selected_rows if row.return_1y is not None]),
        universe_6m=_mean(universe_returns[SIX_MONTHS]),
        universe_1y=_mean(universe_returns[ONE_YEAR]),
        benchmark_ticker=benchmark_ticker,
        benchmark_6m=benchmark_returns[SIX_MONTHS],
        benchmark_1y=benchmark_returns[ONE_YEAR],
        notes=_notes(as_of, horizons, len(stocks), len(records), tolerance_days),
    )
    return BacktestResult(as_of=as_of, records=records, selected=selected_rows, summary=summary)


def _benchmark_entry(store: SnapshotStore, ticker: str, as_of: date) -> float | None:
    quote = store.price_on_or_before(ticker, as_of)
    if quote is None:
        return None
    return quote[1]


def _horizon_returns(
    store: SnapshotStore,
    ticker: str,
    entry_date: date,
    entry_price: float | None,
    horizons: dict[int, date],
    tolerance_days: int,
) -> dict[int, float | None]:
    results: dict[int, float | None] = {}
    for months, horizon in horizons.items():
        if entry_price is None:
            results[months] = None
            continue
        exit_quote = qualifying_exit(
            store.price_on_or_before(ticker, horizon),
            entry_date=entry_date,
            horizon=horizon,
            tolerance_days=tolerance_days,
        )
        if exit_quote is None:
            results[months] = None
            continue
        results[months] = price_return(entry_price, exit_quote[1])
    return results


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _notes(
    as_of: date,
    horizons: dict[int, date],
    snapshots: int,
    valued: int,
    tolerance_days: int,
) -> list[str]:
    notes = [
        "Equal-weight price return. Dividends, trading costs, and delistings are omitted.",
        "This is not a portfolio. Each selected name counts once.",
        "Fundamentals are the latest snapshot stored on or before the as-of date.",
        f"A forward close counts when it falls within {tolerance_days} days of the horizon.",
    ]
    if snapshots == 0:
        notes.append("No point-in-time fundamentals are stored on or before this date.")
    elif valued == 0:
        notes.append("Snapshots exist, but none had a positive close on or before this date.")
    for months, horizon in horizons.items():
        label = "6-month" if months == SIX_MONTHS else "1-year"
        notes.append(f"The {label} horizon is {horizon.isoformat()}.")
    return notes


def _month_length(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31
