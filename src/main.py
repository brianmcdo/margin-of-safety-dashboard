"""Command-line entry point for a margin-of-safety scan."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from src import __version__
from src.backtest.engine import ONE_YEAR, BacktestResult, add_months, run_historical_screen
from src.backtest.history import fetch_closes
from src.config import Settings, load_settings, resolve_path
from src.data.cache import SnapshotStore
from src.data.models import FetchFailure, SignalRecord
from src.logging_setup import configure_logging
from src.pipeline import run_pipeline
from src.portfolio.builder import build_portfolio
from src.signals.generator import NONE, rank_records, records_to_rows

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Screen equities and rank them by margin of safety."
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="sample, sp500, a comma-separated ticker list, or a path to a ticker file",
    )
    parser.add_argument(
        "--min-mos",
        type=float,
        default=None,
        help="Minimum median margin of safety for Strong and Moderate names (0.30 means 30%%)",
    )
    parser.add_argument(
        "--min-quality",
        type=float,
        default=None,
        help="Minimum quality score for Strong and Moderate names (40 means 40)",
    )
    parser.add_argument(
        "--signal",
        choices=["strong", "moderate", "deep", "watchlist", "avoid", "all"],
        default=None,
        help="Keep one label. Default is strong, moderate, and deep value",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore the SQLite cache")
    parser.add_argument("--limit", type=int, default=None, help="Value at most this many tickers")
    parser.add_argument("--output", default=None, help="CSV path. Defaults to output/signals_<run>.csv")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on the cron schedule in config.yaml until interrupted",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Console log level. The log file stays at logging.file_level (DEBUG by default)",
    )
    parser.add_argument("--log-file", default=None, help="Log file path. Defaults to logs/value_signals.log")
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="Replay stored snapshots as of this date and print 6-month and 1-year price returns",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Price benchmark ticker. Defaults to backtest.benchmark in config.yaml",
    )
    parser.add_argument(
        "--fetch-prices",
        action="store_true",
        help="Download missing daily closes from Yahoo before the historical screen",
    )
    parser.add_argument(
        "--max-sector-weight",
        type=float,
        default=None,
        help="Largest share of the suggested basket in one sector (0.25 means 25%%)",
    )
    parser.add_argument(
        "--max-position",
        type=float,
        default=None,
        help="Largest share of the suggested basket in one name (0.10 means 10%%)",
    )
    parser.add_argument(
        "--min-holdings",
        type=int,
        default=None,
        help="Minimum number of names in the suggested basket",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        configure_logging(console_level=args.log_level or "INFO", log_file=args.log_file)
        console.print(f"[red]Configuration error:[/red] {exc}")
        return 1
    configure_logging(
        console_level=args.log_level or settings.logging.level,
        file_level=settings.logging.file_level,
        log_file=args.log_file or settings.logging.file,
    )
    if args.schedule:
        return _schedule(args, settings)
    if args.as_of:
        return _run_backtest(args, settings)
    return _run_once(args, settings)


def _run_once(args: argparse.Namespace, settings: Settings) -> int:
    min_mos = args.min_mos if args.min_mos is not None else settings.valuation.mos.minimum
    min_quality = args.min_quality if args.min_quality is not None else settings.scoring.quality.minimum
    try:
        result = run_pipeline(
            settings,
            universe=args.universe,
            refresh=args.refresh,
            limit=args.limit,
            progress=_cli_progress,
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Scan failed")
        console.print(f"[red]Scan failed:[/red] {exc}")
        return 1

    ranked = rank_records(result.records, min_mos, min_quality=min_quality, signal=args.signal)
    output = _write_csv(result.records, args.output, result.run_id)
    _print_table(ranked, min_mos, min_quality, args.signal)
    _print_portfolio(ranked, args, settings)
    _print_summary(
        result.records,
        result.failures,
        ranked,
        output,
        result.aaa_yield,
        result.new_strong_value,
    )
    if result.records or not result.failures:
        return 0
    return 1


def _run_backtest(args: argparse.Namespace, settings: Settings) -> int:
    try:
        as_of = date.fromisoformat(args.as_of)
    except ValueError:
        console.print(f"[red]--as-of must be YYYY-MM-DD.[/red] Got {args.as_of!r}.")
        return 1
    store = SnapshotStore(str(resolve_path(settings.data.sqlite_path)))
    if args.fetch_prices:
        _fetch_backtest_prices(store, as_of, args.benchmark)
    result = run_historical_screen(
        store,
        settings,
        as_of,
        min_mos=args.min_mos,
        min_quality=args.min_quality,
        signal=args.signal,
        benchmark=args.benchmark,
        limit=args.limit,
    )
    output = _write_backtest_csv(result, args.output)
    _print_backtest(result)
    console.print(f"Wrote {output}")
    console.print(
        "[dim]Equal-weight price returns on stored snapshots. Not a portfolio and not investment advice.[/dim]"
    )
    if result.summary.universe_size == 0:
        return 1
    return 0


def _fetch_backtest_prices(store: SnapshotStore, as_of: date, benchmark: str) -> None:
    tickers = [stock.ticker for stock in store.fundamentals_as_of(as_of)]
    tickers.append(benchmark)
    end = min(
        date.today(),
        add_months(as_of, ONE_YEAR) + timedelta(days=settings.backtest.price_tolerance_days),
    )
    if end < as_of:
        return
    logging.getLogger(__name__).info("Downloading closes for %s names from %s to %s", len(set(tickers)), as_of, end)
    bars = fetch_closes(tickers, as_of - timedelta(days=7), end)
    for ticker, series in bars.items():
        store.save_prices(ticker, series)
    console.print(f"Stored closes for {len(bars)} names.")


def _write_backtest_csv(result: BacktestResult, output: str | None) -> Path:
    frame = pd.DataFrame(
        [
            {
                "ticker": row.ticker,
                "name": row.name,
                "signal": row.signal,
                "score": row.composite_score,
                "margin_of_safety": row.margin_of_safety,
                "entry_date": row.entry_date.isoformat(),
                "entry_price": row.entry_price,
                "return_6m": row.return_6m,
                "return_1y": row.return_1y,
            }
            for row in result.selected
        ]
    )
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = resolve_path(path)
    else:
        path = resolve_path("output") / f"backtest_{result.as_of.isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _print_backtest(result: BacktestResult) -> None:
    summary = result.summary
    console.print(
        f"Historical screen as of {result.as_of.isoformat()}. "
        f"Valued {summary.universe_size} names. Selected {summary.selected_count}."
    )
    _print_horizon("6-month", summary.selected_6m, summary.universe_6m, summary.excess_6m, summary.priced_6m, summary.benchmark_ticker, summary.benchmark_6m)
    _print_horizon("1-year", summary.selected_1y, summary.universe_1y, summary.excess_1y, summary.priced_1y, summary.benchmark_ticker, summary.benchmark_1y)
    table = Table(title="Selected names")
    for column in ("Ticker", "Signal", "Score", "MoS", "Entry", "6-month", "1-year"):
        table.add_column(column)
    for row in result.selected[:40]:
        table.add_row(
            row.ticker,
            row.signal,
            f"{row.composite_score:.1f}",
            _pct(row.margin_of_safety),
            _px(row.entry_price),
            _pct(row.return_6m),
            _pct(row.return_1y),
        )
    if result.selected:
        console.print(table)
        if len(result.selected) > 40:
            console.print(f"Showing 40 of {len(result.selected)}. The CSV has the full list.")
    for note in summary.notes:
        console.print(f"[dim]{note}[/dim]")


def _print_horizon(
    label: str,
    selected: float | None,
    universe: float | None,
    excess: float | None,
    count: int,
    benchmark: str,
    benchmark_return: float | None,
) -> None:
    if selected is None and universe is None and benchmark_return is None:
        console.print(f"{label}: no stored close near this horizon yet.")
        return
    parts = [
        f"{label}: selected {_pct(selected)} ({count} names)",
        f"universe {_pct(universe)}",
        f"excess {_pct(excess)}",
        f"{benchmark} {_pct(benchmark_return)}",
    ]
    console.print(", ".join(parts) + ".")


def _cli_progress(done: int, total: int, ticker: str) -> None:
    if total <= 0 or done == total or done % 10 == 0:
        logging.getLogger(__name__).info("Fetched %s/%s (%s)", done, total, ticker)


def _write_csv(records: list[SignalRecord], output: str | None, run_id: str) -> Path:
    frame = pd.DataFrame(records_to_rows(records))
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = resolve_path(path)
    else:
        path = resolve_path("output") / f"signals_{run_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _print_table(ranked: list[SignalRecord], min_mos: float, min_quality: float, signal: str | None) -> None:
    label = signal or "strong, moderate, deep"
    table = Table(
        title=f"Ranked value signals ({label}; margin of safety ≥ {min_mos:.0%}; quality ≥ {min_quality:.0f})"
    )
    for column in ("Ticker", "Signal", "Score", "Quality", "F", "Z", "ROIC", "MoS", "Price", "Graham", "DCF", "News"):
        table.add_column(column)
    for record in ranked[:40]:
        table.add_row(
            record.ticker,
            record.signal,
            f"{record.composite_score:.1f}",
            "—" if record.quality_score is None else f"{record.quality_score:.0f}",
            _f_score(record),
            "—" if record.altman_z is None else f"{record.altman_z:.2f}",
            _pct(record.roic if record.roic is not None else record.roe),
            _pct(record.margin_of_safety),
            _px(record.price),
            _px(record.graham_number),
            _px(record.dcf_value),
            "Neg" if record.negative_news else "",
        )
    if ranked:
        console.print(table)
        if len(ranked) > 40:
            console.print(f"Showing 40 of {len(ranked)}. The CSV has the full ranking.")
    else:
        console.print("[yellow]No names cleared the selected filters.[/yellow]")


def _print_portfolio(ranked: list[SignalRecord], args: argparse.Namespace, settings: Settings) -> None:
    if not ranked:
        return
    book = settings.portfolio
    max_sector = args.max_sector_weight if args.max_sector_weight is not None else book.max_sector_weight
    max_position = args.max_position if args.max_position is not None else book.max_position_size
    min_holdings = args.min_holdings if args.min_holdings is not None else book.min_holdings
    try:
        basket = build_portfolio(
            ranked,
            max_sector_weight=max_sector,
            max_position_size=max_position,
            min_holdings=min_holdings,
        )
    except ValueError as exc:
        console.print(f"[red]Basket skipped:[/red] {exc}")
        return
    table = Table(
        title=(
            f"Suggested basket (max sector {max_sector:.0%}, "
            f"max position {max_position:.0%}, minimum {min_holdings} holdings)"
        )
    )
    for column in ("Ticker", "Sector", "Signal", "Score", "Weight"):
        table.add_column(column)
    for holding in basket.holdings:
        table.add_row(
            holding.ticker,
            holding.sector,
            holding.signal,
            f"{holding.composite_score:.1f}",
            f"{holding.weight:.1%}",
        )
    if basket.holdings:
        console.print(table)
    for note in basket.notes:
        console.print(f"[yellow]{note}[/yellow]")
    console.print("[dim]The basket is a rank-order sketch under those caps. Not investment advice.[/dim]")


def _print_summary(
    records: list[SignalRecord],
    failures: list[FetchFailure],
    ranked: list[SignalRecord],
    output: Path,
    aaa_yield: float,
    new_strong_value: list[str],
) -> None:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.signal] = counts.get(record.signal, 0) + 1
    counted = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    console.print(
        f"Valued {len(records)} names. {counted or 'no results'}. "
        f"Ranked {len(ranked)}. AAA yield {aaa_yield:.2f}%."
    )
    if failures:
        console.print(f"Fetch failures: {len(failures)}. First: {failures[0].ticker} ({failures[0].error})")
    rejected: dict[str, int] = {}
    for record in records:
        if record.signal != NONE:
            continue
        for failure in record.screen_failures:
            key = failure.split(" is ")[0]
            rejected[key] = rejected.get(key, 0) + 1
    if rejected:
        top = sorted(rejected.items(), key=lambda item: (-item[1], item[0]))[:6]
        summary = "; ".join(f"{name} ({count})" for name, count in top)
        console.print(f"Most common screen rejects: {summary}")
    if new_strong_value:
        console.print("New Strong Value since the prior daily snapshot: " + ", ".join(new_strong_value))
    console.print(f"Wrote {output}")
    console.print(
        "[dim]Research signals only. Assumptions and data quality dominate the result. Not investment advice.[/dim]"
    )


def _f_score(record: SignalRecord) -> str:
    if record.f_score is None or record.f_score_available <= 0:
        return "—"
    return f"{record.f_score}/{record.f_score_available}"


def _px(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


def _schedule(args: argparse.Namespace, settings: Settings) -> int:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler()
    trigger = CronTrigger(
        day_of_week=settings.schedule.day_of_week,
        hour=settings.schedule.hour,
        minute=settings.schedule.minute,
    )
    scheduler.add_job(lambda: _run_once(args, settings), trigger, name="value-signals")
    console.print(
        f"value-signals {__version__} scheduled "
        f"{settings.schedule.day_of_week} at "
        f"{settings.schedule.hour:02d}:{settings.schedule.minute:02d}. Ctrl-C to stop."
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
