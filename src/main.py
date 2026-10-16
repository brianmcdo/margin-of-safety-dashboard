"""Command-line entry point for a margin-of-safety scan."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from src import __version__
from src.config import load_settings, resolve_path
from src.pipeline import run_pipeline
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
        help="Minimum median margin of safety for the ranked table (0.30 means 30%%)",
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
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        return 1
    if args.schedule:
        return _schedule(args, settings)
    return _run_once(args, settings)


def _run_once(args: argparse.Namespace, settings) -> int:
    min_mos = args.min_mos if args.min_mos is not None else settings.valuation.mos.minimum
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

    ranked = rank_records(result.records, min_mos)
    output = _write_csv(result.records, args.output, result.run_id)
    _print_table(ranked, min_mos)
    _print_summary(result.records, result.failures, ranked, output, result.aaa_yield)
    if result.records or not result.failures:
        return 0
    return 1


def _cli_progress(done: int, total: int, ticker: str) -> None:
    if total <= 0 or done == total or done % 10 == 0:
        logging.getLogger(__name__).info("Fetched %s/%s (%s)", done, total, ticker)


def _write_csv(records, output: str | None, run_id: str) -> Path:
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


def _print_table(ranked, min_mos: float) -> None:
    table = Table(title=f"Ranked value signals (margin of safety ≥ {min_mos:.0%})")
    for column in ("Ticker", "Signal", "Score", "MoS", "Price", "Graham", "Formula", "DCF", "NCAV"):
        table.add_column(column)
    for record in ranked[:40]:
        table.add_row(
            record.ticker,
            record.signal,
            f"{record.composite_score:.1f}",
            _pct(record.margin_of_safety),
            _px(record.price),
            _px(record.graham_number),
            _px(record.graham_formula),
            _px(record.dcf_value),
            _px(record.ncav_per_share),
        )
    if ranked:
        console.print(table)
        if len(ranked) > 40:
            console.print(f"Showing 40 of {len(ranked)}. The CSV has the full ranking.")
    else:
        console.print("[yellow]No names cleared the margin-of-safety threshold.[/yellow]")


def _print_summary(records, failures, ranked, output: Path, aaa_yield: float) -> None:
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
    console.print(f"Wrote {output}")
    console.print(
        "[dim]Research signals only. Assumptions and data quality dominate the result. Not investment advice.[/dim]"
    )


def _px(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


def _schedule(args: argparse.Namespace, settings) -> int:
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
