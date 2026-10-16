"""End-to-end run: fetch, screen, value, score, store, and alert."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from src.config import Settings
from src.data.cache import SnapshotStore
from src.data.fetcher import fetch_aaa_yield, fetch_universe, resolve_universe
from src.data.models import FetchFailure, PipelineResult, StockFundamentals, utcnow
from src.screening.filters import attach_peer_percentiles, compute_metrics, screen_stock
from src.signals.alerts import emit_alerts
from src.signals.generator import build_record

logger = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


def run_pipeline(
    settings: Settings,
    *,
    universe: str | None = None,
    tickers: list[str] | None = None,
    refresh: bool = False,
    limit: int | None = None,
    fundamentals: list[StockFundamentals] | None = None,
    aaa_yield: float | None = None,
    store: SnapshotStore | None = None,
    progress: Progress | None = None,
) -> PipelineResult:
    """Run one snapshot and persist it.

    Pass ``fundamentals`` to value a prepared set without touching the network.
    Every ticker that has a price is valued, including names that fail the
    screen, so later runs and the dashboard can show why a name was excluded.
    """
    database = store or SnapshotStore(str(_sqlite_path(settings)))
    if tickers is not None:
        label = universe or "custom"
        symbols = tickers
    elif fundamentals is not None:
        label = universe or "provided"
        symbols = [item.ticker for item in fundamentals]
    else:
        label, symbols = resolve_universe(universe or settings.universe.default, settings, database)
    if limit is not None and limit >= 0:
        symbols = symbols[:limit]

    failures: list[FetchFailure] = []
    if fundamentals is None:
        stocks, failures = fetch_universe(
            symbols,
            settings,
            database,
            refresh=refresh,
            progress=progress,
        )
    else:
        stocks = list(fundamentals)

    yield_percent = aaa_yield if aaa_yield is not None else fetch_aaa_yield(settings, database)
    usable: list[StockFundamentals] = []
    for stock in stocks:
        if stock.price is None or stock.price <= 0:
            failures.append(FetchFailure(ticker=stock.ticker, error="no positive price"))
            continue
        usable.append(stock)

    metrics = {stock.ticker: compute_metrics(stock, settings) for stock in usable}
    attach_peer_percentiles(metrics, usable)
    records = [
        build_record(stock, screen_stock(stock, metrics[stock.ticker], settings), settings, yield_percent)
        for stock in usable
    ]
    previous = database.latest_run()
    result = PipelineResult(
        run_id=utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8],
        created_at=utcnow(),
        universe=label,
        records=records,
        failures=failures,
        aaa_yield=yield_percent,
    )
    database.save_run(result)
    if previous is None:
        logger.info("First snapshot stored. New-candidate alerts start on the next run.")
    else:
        previous_tickers = {
            record.ticker
            for record in previous.records
            if record.signal in {"Strong Value", "Moderate Value", "Deep Value / Net-Net"}
        }
        emit_alerts(records, previous_tickers, settings)
    logger.info(
        "Run %s valued %s names (%s fetch failures), AAA yield %.2f%%",
        result.run_id,
        len(records),
        len(failures),
        yield_percent,
    )
    return result


def _sqlite_path(settings: Settings):
    from src.config import resolve_path

    return resolve_path(settings.data.sqlite_path)
