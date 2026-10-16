"""Read the saved snapshot and run the same actions the dashboard used to."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.backtest.engine import ONE_YEAR, add_months, run_historical_screen
from src.backtest.history import fetch_closes
from src.config import load_settings, project_root, resolve_path
from src.data.cache import SnapshotStore
from src.logging_setup import configure_logging
from src.pipeline import run_pipeline
from src.portfolio.builder import build_portfolio
from src.screening.filters import compute_metrics
from src.valuation.commodity import (
    assumption_text,
    classify_producer,
    commodity_nav_per_share,
    commodity_sensitivity,
    is_low_cost_producer,
)
from src.valuation.quality import assess_quality

logger = logging.getLogger(__name__)
DIST = project_root() / "web" / "dist"


class ScanRequest(BaseModel):
    universe: str = "sample"
    limit: int | None = None
    refresh: bool = False


class PortfolioRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    max_sector_weight: float = 0.25
    max_position_size: float = 0.10
    min_holdings: int = 8


class CommodityRequest(BaseModel):
    ticker: str
    oil: float = 70
    gold: float = 2000
    copper: float = 4.0


class HistoryRequest(BaseModel):
    as_of: date
    min_mos: float = 0.30
    min_quality: float = 40
    signal: str | None = None
    benchmark: str = "SPY"
    fetch_prices: bool = False


def _settings():
    return load_settings()


def _store(settings):
    return SnapshotStore(str(resolve_path(settings.data.sqlite_path)))


def _quote(settings, name: str) -> float:
    quote = settings.commodity.prices.get(name)
    return 0.0 if quote is None else float(quote.normalized)


def _context(settings, aaa_yield: float) -> dict:
    valuation = settings.valuation
    dcf = valuation.dcf
    weights = settings.scoring.weights
    quality = settings.scoring.quality
    return {
        "aaa_yield": aaa_yield,
        "graham_growth_percent": valuation.graham_growth_percent,
        "max_graham_growth_percent": valuation.max_graham_growth_percent,
        "max_graham_implied_pe": valuation.max_graham_implied_pe,
        "discount_receivables": valuation.ncav.discount_receivables,
        "discount_inventory": valuation.ncav.discount_inventory,
        "normalize_years": dcf.normalize_years,
        "growth_rate": dcf.growth_rate,
        "years": dcf.years,
        "discount_rate": dcf.discount_rate,
        "terminal_growth": dcf.terminal_growth,
        "max_terminal_share": dcf.max_terminal_share,
        "growth_spread": dcf.growth_spread,
        "discount_spread": dcf.discount_spread,
        "weight_margin": weights.margin_of_safety,
        "weight_cheapness": weights.cheapness,
        "weight_quality": weights.quality,
        "quality_minimum": quality.minimum,
        "altman_distress": quality.altman_distress,
        "low_return_threshold": quality.low_return_threshold,
        "low_cost_min_quality": settings.commodity.low_cost_min_quality,
        "low_cost_max_debt_equity": settings.commodity.low_cost_max_debt_equity,
        "mos_minimum": valuation.mos.minimum,
        "portfolio": settings.portfolio.model_dump(),
        "oil": _quote(settings, "oil"),
        "gold": _quote(settings, "gold"),
        "copper": _quote(settings, "copper"),
        "benchmark": settings.backtest.benchmark,
        "assumption": assumption_text(settings.commodity),
    }


def _refresh_low_cost(records, settings, store):
    refreshed = []
    for record in records:
        if not record.is_commodity_producer:
            refreshed.append(record)
            continue
        stock = store.get_fundamentals(record.ticker, 24 * 365, allow_expired=True)
        if stock is None:
            refreshed.append(record)
            continue
        quality = assess_quality(stock, settings.scoring.quality)
        metrics = compute_metrics(stock, settings)
        flag = is_low_cost_producer(
            is_producer=True,
            quality_score=quality.quality_score,
            low_returns=quality.low_returns,
            bankruptcy_risk=quality.bankruptcy_risk,
            free_cash_flow=stock.free_cash_flow,
            debt_to_equity=metrics.debt_to_equity,
            settings=settings.commodity,
        )
        refreshed.append(record.model_copy(update={"low_cost_producer": flag}))
    return refreshed


def create_app() -> FastAPI:
    settings = _settings()
    configure_logging(
        console_level=settings.logging.level,
        file_level=settings.logging.file_level,
        log_file=settings.logging.file,
    )
    app = FastAPI(title="Margin of Safety", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/snapshot")
    def snapshot() -> dict:
        current = _settings()
        store = _store(current)
        result = store.latest_run()
        if result is None or not result.records:
            return {
                "run": None,
                "records": [],
                "context": _context(current, current.fred.fallback_aaa_yield),
            }
        records = _refresh_low_cost(result.records, current, store)
        return {
            "run": {
                "run_id": result.run_id,
                "created_at": result.created_at.isoformat(),
                "universe": result.universe,
                "aaa_yield": result.aaa_yield,
                "failures": len(result.failures),
            },
            "records": [record.model_dump(mode="json") for record in records],
            "context": _context(current, result.aaa_yield),
        }

    @app.post("/api/scan")
    def scan(body: ScanRequest) -> dict:
        current = _settings()
        try:
            result = run_pipeline(
                current,
                universe=body.universe.strip() or "sample",
                refresh=body.refresh,
                limit=None if body.limit in (None, 0) else body.limit,
            )
        except Exception as exc:
            logger.exception("Scan failed")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run_id": result.run_id,
            "universe": result.universe,
            "valued": len(result.records),
            "failures": len(result.failures),
        }

    @app.post("/api/portfolio")
    def portfolio(body: PortfolioRequest) -> dict:
        current = _settings()
        result = _store(current).latest_run()
        by_ticker = {} if result is None else {record.ticker: record for record in result.records}
        ordered = [by_ticker[ticker] for ticker in body.tickers if ticker in by_ticker]
        try:
            basket = build_portfolio(
                ordered,
                max_sector_weight=body.max_sector_weight,
                max_position_size=body.max_position_size,
                min_holdings=body.min_holdings,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "invested": basket.invested,
            "notes": basket.notes,
            "sectors": basket.sector_weights(),
            "holdings": [
                {
                    "ticker": holding.ticker,
                    "name": holding.name,
                    "sector": holding.sector,
                    "signal": holding.signal,
                    "composite_score": holding.composite_score,
                    "weight": holding.weight,
                }
                for holding in basket.holdings
            ],
        }

    @app.post("/api/commodity")
    def commodity(body: CommodityRequest) -> dict:
        current = _settings()
        store = _store(current)
        stock = store.get_fundamentals(body.ticker, 24 * 365, allow_expired=True)
        profile = classify_producer(stock, current.commodity) if stock is not None else None
        if stock is None or profile is None:
            return {"producer": False, "nav": None, "note": "", "unit": "", "cells": []}
        overrides = {"oil": body.oil, "gold": body.gold, "copper": body.copper}
        nav, note = commodity_nav_per_share(
            stock,
            current.commodity,
            current.valuation.dcf,
            normalized_overrides=overrides,
        )
        cells = commodity_sensitivity(
            stock,
            current.commodity,
            current.valuation.dcf,
            normalized_overrides=overrides,
        )
        return {
            "producer": True,
            "nav": nav,
            "note": note,
            "unit": profile.unit,
            "cells": [cell.model_dump() for cell in cells],
        }

    @app.get("/api/history/span")
    def history_span() -> dict:
        current = _settings()
        span = _store(current).pit_date_span()
        if span is None:
            return {"first": None, "last": None}
        first, last = span
        return {"first": first.isoformat(), "last": last.isoformat()}

    @app.post("/api/history")
    def history(body: HistoryRequest) -> dict:
        current = _settings()
        store = _store(current)
        benchmark = body.benchmark.strip() or current.backtest.benchmark or "SPY"
        if body.fetch_prices:
            tickers = [stock.ticker for stock in store.fundamentals_as_of(body.as_of)]
            tickers.append(benchmark)
            end = min(
                date.today(),
                add_months(body.as_of, ONE_YEAR) + timedelta(days=current.backtest.price_tolerance_days),
            )
            bars = fetch_closes(tickers, body.as_of - timedelta(days=7), end)
            for ticker, series in bars.items():
                store.save_prices(ticker, series)
        result = run_historical_screen(
            store,
            current,
            body.as_of,
            min_mos=body.min_mos,
            min_quality=body.min_quality,
            signal=body.signal,
            benchmark=benchmark,
        )
        summary = result.summary
        return {
            "as_of": result.as_of.isoformat(),
            "summary": {
                "universe_size": summary.universe_size,
                "selected_count": summary.selected_count,
                "selected_6m": summary.selected_6m,
                "selected_1y": summary.selected_1y,
                "universe_6m": summary.universe_6m,
                "universe_1y": summary.universe_1y,
                "excess_6m": summary.excess_6m,
                "excess_1y": summary.excess_1y,
                "benchmark_ticker": summary.benchmark_ticker,
                "benchmark_6m": summary.benchmark_6m,
                "benchmark_1y": summary.benchmark_1y,
                "notes": summary.notes,
            },
            "selected": [
                {
                    "ticker": row.ticker,
                    "name": row.name,
                    "signal": row.signal,
                    "composite_score": row.composite_score,
                    "margin_of_safety": row.margin_of_safety,
                    "entry_price": row.entry_price,
                    "return_6m": row.return_6m,
                    "return_1y": row.return_1y,
                }
                for row in result.selected
            ],
        }

    if DIST.is_dir():
        assets = DIST / "assets"
        if assets.is_dir():
            from fastapi.staticfiles import StaticFiles

            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api"):
                raise HTTPException(status_code=404)
            candidate = DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(DIST / "index.html")

    return app


app = create_app()
