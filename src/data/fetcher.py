"""Retrieve prices and fundamentals, with a local cache in front of the network."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from io import StringIO
from typing import Any

import pandas as pd
import requests

from src.config import Settings, resolve_path
from src.data.cache import SnapshotStore
from src.data.models import FetchFailure, StockFundamentals, as_float
from src.data.parse import (
    LINE_ITEMS,
    annual_series,
    assemble_fundamentals,
    info_number,
    latest_line_item,
)
from src.data.universe import SAMPLE_UNIVERSE, normalize_ticker

logger = logging.getLogger(__name__)

USER_AGENT = "value-signals/1.0 (educational research; local cache)"
SP500_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)
Progress = Callable[[int, int, str], None]


def _redact(message: str) -> str:
    return re.sub(r"(apikey=)[^&\s]+", r"\1***", message, flags=re.IGNORECASE)


def resolve_source(settings: Settings) -> str:
    """Pick yfinance or FMP. Missing FMP credentials fall back to yfinance."""
    source = settings.data.source.strip().lower()
    has_fmp_key = bool(os.environ.get(settings.data.fmp_api_key_env))
    if source == "auto":
        return "fmp" if has_fmp_key else "yfinance"
    if source == "fmp" and not has_fmp_key:
        logger.warning("FMP_API_KEY is not set; falling back to yfinance")
        return "yfinance"
    if source not in {"yfinance", "fmp"}:
        logger.warning("Unknown data source %s; using yfinance", source)
        return "yfinance"
    return source


def resolve_universe(name: str, settings: Settings, store: SnapshotStore) -> tuple[str, list[str]]:
    """Turn a CLI universe into a label and a ticker list.

    Accepted forms: ``sample``, ``sp500``, a comma-separated list, or a text
    file with one ticker per line.
    """
    raw = (name or settings.universe.default).strip()
    path = resolve_path(raw)
    if path.is_file():
        tickers = [
            normalize_ticker(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return path.name, _dedupe(tickers)
    lowered = raw.lower()
    if lowered == "sample":
        return "sample", list(SAMPLE_UNIVERSE)
    if lowered in {"sp500", "s&p500", "s&p 500"}:
        return "sp500", load_sp500(store, settings.data.request_timeout_seconds)
    tickers = [normalize_ticker(part) for part in raw.split(",") if part.strip()]
    if not tickers:
        raise ValueError(f"Could not resolve universe {raw!r}")
    return "custom", _dedupe(tickers)


def _dedupe(tickers: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for ticker in tickers:
        if ticker and ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


def load_sp500(store: SnapshotStore, timeout: float) -> list[str]:
    """S&P 500 constituents, cached for a week."""
    cached = store.get_ticker_list("sp500", ttl_hours=24 * 7)
    if cached:
        return cached
    errors: list[str] = []
    for loader in (_sp500_from_wikipedia, _sp500_from_csv):
        try:
            tickers = loader(timeout)
            if len(tickers) < 400:
                raise RuntimeError(f"only {len(tickers)} symbols returned")
            store.save_ticker_list("sp500", tickers)
            logger.info("Loaded %s S&P 500 tickers", len(tickers))
            return tickers
        except Exception as exc:
            errors.append(_redact(str(exc)))
            logger.warning("S&P 500 source failed: %s", _redact(str(exc)))
    raise RuntimeError("Could not load the S&P 500 universe: " + "; ".join(errors))


def _http_get(url: str, timeout: float) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    return response.text


def _sp500_from_wikipedia(timeout: float) -> list[str]:
    html = _http_get(SP500_WIKI, timeout)
    tables = pd.read_html(StringIO(html))
    frame = tables[0]
    column = "Symbol" if "Symbol" in frame.columns else frame.columns[0]
    return _dedupe([normalize_ticker(str(symbol)) for symbol in frame[column].tolist()])


def _sp500_from_csv(timeout: float) -> list[str]:
    frame = pd.read_csv(StringIO(_http_get(SP500_CSV, timeout)))
    column = "Symbol" if "Symbol" in frame.columns else frame.columns[0]
    return _dedupe([normalize_ticker(str(symbol)) for symbol in frame[column].tolist()])


def fetch_aaa_yield(settings: Settings, store: SnapshotStore | None = None) -> float:
    """Latest Moody's AAA corporate yield in percent, with a configured fallback.

    Graham's formula uses the yield as a percent (6.2, not 0.062). FRED series
    DAAA is already in percent.
    """
    cache_key = f"fred:{settings.fred.series}"
    if store is not None:
        cached = store.get_meta(cache_key, ttl_hours=12)
        cached_value = as_float(cached)
        if cached_value is not None and cached_value > 0:
            return cached_value
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={settings.fred.series}"
    try:
        # A short timeout: a missing yield should fall back, not stall the scan.
        text = _http_get(url, min(8.0, settings.data.request_timeout_seconds))
        yield_percent = _last_fred_value(text)
    except Exception as exc:
        logger.warning(
            "FRED AAA yield unavailable (%s); using fallback %.2f%%",
            _redact(str(exc)),
            settings.fred.fallback_aaa_yield,
        )
        return settings.fred.fallback_aaa_yield
    if store is not None:
        store.set_meta(cache_key, f"{yield_percent:.6f}")
    logger.info("AAA corporate yield from FRED: %.2f%%", yield_percent)
    return yield_percent


def _last_fred_value(csv_text: str) -> float:
    frame = pd.read_csv(StringIO(csv_text))
    if frame.empty or frame.shape[1] < 2:
        raise RuntimeError("FRED response did not contain a yield series")
    for raw in reversed(frame.iloc[:, 1].tolist()):
        number = as_float(raw)
        if number is not None and number > 0:
            return number
    raise RuntimeError("FRED series had no positive observations")


def fetch_universe(
    tickers: list[str],
    settings: Settings,
    store: SnapshotStore,
    *,
    refresh: bool = False,
    progress: Progress | None = None,
) -> tuple[list[StockFundamentals], list[FetchFailure]]:
    """Fetch every ticker, using the SQLite cache unless refresh is set."""
    source = resolve_source(settings)
    fresh: list[StockFundamentals] = []
    needed: list[str] = []
    ttl = settings.universe.cache_ttl_hours
    for ticker in tickers:
        cached = None if refresh else store.get_fundamentals(ticker, ttl)
        if cached is not None and cached.price is not None:
            fresh.append(cached)
        else:
            needed.append(ticker)
    failures: list[FetchFailure] = []
    done = len(fresh)
    total = len(tickers)
    if progress is not None:
        progress(done, total, "cache")
    if not needed:
        return sorted(fresh, key=lambda item: item.ticker), failures

    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max(1, min(settings.universe.max_workers, len(needed)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_with_retry, ticker, settings, source): ticker for ticker in needed
        }
        for future in as_completed(futures):
            ticker = futures[future]
            done += 1
            try:
                stock = future.result()
            except Exception as exc:
                message = _redact(str(exc)) or exc.__class__.__name__
                logger.warning("Failed to fetch %s: %s", ticker, message)
                failures.append(FetchFailure(ticker=ticker, error=message))
            else:
                try:
                    store.save_fundamentals(stock)
                except Exception:
                    logger.exception("Could not cache %s", ticker)
                fresh.append(stock)
            if progress is not None:
                progress(done, total, ticker)
    fresh.sort(key=lambda item: item.ticker)
    return fresh, failures


def _fetch_with_retry(ticker: str, settings: Settings, source: str) -> StockFundamentals:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            if source == "fmp":
                return fetch_fmp(ticker, settings)
            return fetch_yfinance(ticker)
        except Exception as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_yfinance(ticker: str) -> StockFundamentals:
    """Pull one ticker from Yahoo Finance via yfinance."""
    import yfinance as yf

    symbol = normalize_ticker(ticker)
    client = yf.Ticker(symbol)
    notes: list[str] = []
    info: dict[str, Any] = {}
    try:
        raw_info = client.info
        if isinstance(raw_info, dict):
            info = raw_info
    except Exception as exc:
        notes.append(f"quote info unavailable: {exc.__class__.__name__}")

    price = info_number(info, "currentPrice", "regularMarketPrice")
    if price is None:
        try:
            fast = client.fast_info
            price = as_float(fast.get("lastPrice") if hasattr(fast, "get") else None)
            if price is None:
                price = as_float(getattr(fast, "last_price", None))
        except Exception:
            price = None
    if price is None:
        try:
            history = client.history(period="5d")
            if history is not None and not history.empty and "Close" in history.columns:
                price = as_float(history["Close"].iloc[-1])
                notes.append("price taken from recent daily history")
        except Exception:
            price = None
    if price is None or price <= 0:
        raise RuntimeError(f"no price returned for {symbol}")

    balance = _safe_frame(lambda: client.balance_sheet, notes, "balance sheet")
    income = _safe_frame(lambda: client.financials, notes, "income statement")
    cashflow = _safe_frame(lambda: client.cashflow, notes, "cash flow statement")
    fcf_history = _free_cash_flow_history(cashflow)
    total_equity = latest_line_item(balance, LINE_ITEMS["equity"])
    shares = info_number(info, "sharesOutstanding", "impliedSharesOutstanding")
    if shares is None:
        shares = latest_line_item(balance, LINE_ITEMS["shares"])
    free_cash_flow = info_number(info, "freeCashflow")
    if free_cash_flow is None and fcf_history:
        free_cash_flow = fcf_history[0]
        notes.append("free cash flow fell back to the latest annual figure")

    return assemble_fundamentals(
        ticker=symbol,
        name=_text(info, "shortName", "longName", "displayName"),
        sector=_text(info, "sector"),
        industry=_text(info, "industry"),
        price=price,
        market_cap=info_number(info, "marketCap"),
        shares_outstanding=shares,
        trailing_eps=info_number(info, "trailingEps", "epsTrailingTwelveMonths"),
        book_value_per_share=info_number(info, "bookValue"),
        total_equity=total_equity,
        current_assets=latest_line_item(balance, LINE_ITEMS["current_assets"]),
        current_liabilities=latest_line_item(balance, LINE_ITEMS["current_liabilities"]),
        total_liabilities=latest_line_item(balance, LINE_ITEMS["total_liabilities"]),
        total_debt=_prefer(
            info_number(info, "totalDebt"),
            latest_line_item(balance, LINE_ITEMS["total_debt"]),
        ),
        cash=_prefer(info_number(info, "totalCash"), latest_line_item(balance, LINE_ITEMS["cash"])),
        inventory=latest_line_item(balance, LINE_ITEMS["inventory"]),
        receivables=latest_line_item(balance, LINE_ITEMS["receivables"]),
        free_cash_flow=free_cash_flow,
        fcf_history=fcf_history,
        revenue=latest_line_item(income, LINE_ITEMS["revenue"]),
        ebit=latest_line_item(income, LINE_ITEMS["ebit"]),
        current_ratio=info_number(info, "currentRatio"),
        debt_to_equity_reported=info_number(info, "debtToEquity"),
        debt_to_equity_is_percent=True,
        average_volume=info_number(info, "averageVolume", "averageDailyVolume3Month"),
        enterprise_value=info_number(info, "enterpriseValue"),
        source="yfinance",
        notes=notes,
    )


def _prefer(primary: float | None, fallback: float | None) -> float | None:
    return primary if primary is not None else fallback


def _free_cash_flow_history(cashflow: pd.DataFrame | None) -> list[float]:
    history = annual_series(cashflow, LINE_ITEMS["free_cash_flow"])
    if history:
        return history
    operating = annual_series(cashflow, LINE_ITEMS["operating_cash_flow"])
    capex = annual_series(cashflow, LINE_ITEMS["capital_expenditure"])
    combined: list[float] = []
    for index, operating_value in enumerate(operating):
        if index >= len(capex):
            break
        spend = capex[index]
        # Yahoo stores capital expenditure as a negative use of cash.
        combined.append(operating_value + spend if spend < 0 else operating_value - spend)
    return combined


def _safe_frame(loader: Callable[[], Any], notes: list[str], label: str) -> pd.DataFrame | None:
    try:
        frame = loader()
    except Exception as exc:
        notes.append(f"{label} unavailable: {exc.__class__.__name__}")
        return None
    if not isinstance(frame, pd.DataFrame):
        return None
    return frame


def _text(info: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def fetch_fmp(ticker: str, settings: Settings) -> StockFundamentals:
    """Optional Financial Modeling Prep fetch. Requires FMP_API_KEY."""
    symbol = normalize_ticker(ticker)
    api_key = os.environ.get(settings.data.fmp_api_key_env, "")
    if not api_key:
        raise RuntimeError("FMP_API_KEY is not set")
    timeout = settings.data.request_timeout_seconds
    profile = _fmp_first(symbol, "profile", api_key, timeout)
    metrics = _fmp_first(symbol, "key-metrics-ttm", api_key, timeout)
    balance = _fmp_first(symbol, "balance-sheet-statement", api_key, timeout, {"limit": 1})
    income = _fmp_first(symbol, "income-statement", api_key, timeout, {"limit": 1})
    cash_rows = _fmp_list(symbol, "cash-flow-statement", api_key, timeout, {"limit": 4})
    price = _first_number(profile, "price")
    if price is None or price <= 0:
        raise RuntimeError(f"FMP returned no price for {symbol}")
    fcf_history = [
        number
        for row in cash_rows
        if (number := _first_number(row, "freeCashFlow")) is not None
    ]
    free_cash_flow = fcf_history[0] if fcf_history else None
    per_share_fcf = _first_number(metrics, "freeCashFlowPerShareTTM")
    shares = _first_number(profile, "sharesOutstanding") or _first_number(
        balance, "commonStock", "weightedAverageShsOutDil"
    )
    if free_cash_flow is None and per_share_fcf is not None and shares is not None:
        free_cash_flow = per_share_fcf * shares
    return assemble_fundamentals(
        ticker=symbol,
        name=_first_text(profile, "companyName"),
        sector=_first_text(profile, "sector"),
        industry=_first_text(profile, "industry"),
        price=price,
        market_cap=_first_number(profile, "mktCap"),
        shares_outstanding=shares,
        trailing_eps=_first_number(metrics, "netIncomePerShareTTM")
        or _first_number(income, "epsdiluted", "eps"),
        book_value_per_share=_first_number(metrics, "bookValuePerShareTTM"),
        total_equity=_first_number(balance, "totalStockholdersEquity"),
        current_assets=_first_number(balance, "totalCurrentAssets"),
        current_liabilities=_first_number(balance, "totalCurrentLiabilities"),
        total_liabilities=_first_number(balance, "totalLiabilities"),
        total_debt=_first_number(balance, "totalDebt"),
        cash=_first_number(balance, "cashAndCashEquivalents", "cashAndShortTermInvestments"),
        inventory=_first_number(balance, "inventory"),
        receivables=_first_number(balance, "netReceivables"),
        free_cash_flow=free_cash_flow,
        fcf_history=fcf_history,
        revenue=_first_number(income, "revenue"),
        ebit=_first_number(income, "ebit", "operatingIncome"),
        current_ratio=_first_number(metrics, "currentRatioTTM"),
        debt_to_equity_reported=_first_number(metrics, "debtToEquityTTM"),
        debt_to_equity_is_percent=False,
        average_volume=_first_number(profile, "volAvg"),
        enterprise_value=_first_number(metrics, "enterpriseValueTTM"),
        source="fmp",
    )


def _fmp_url(symbol: str, endpoint: str) -> str:
    return f"https://financialmodelingprep.com/api/v3/{endpoint}/{symbol}"


def _fmp_list(
    symbol: str,
    endpoint: str,
    api_key: str,
    timeout: float,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"apikey": api_key}
    if extra:
        params.update(extra)
    response = requests.get(
        _fmp_url(symbol, endpoint),
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        message = payload.get("Error Message") or payload.get("error") or "unexpected FMP payload"
        raise RuntimeError(str(message))
    if not isinstance(payload, list):
        raise RuntimeError("unexpected FMP payload")
    return [row for row in payload if isinstance(row, dict)]


def _fmp_first(
    symbol: str,
    endpoint: str,
    api_key: str,
    timeout: float,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _fmp_list(symbol, endpoint, api_key, timeout, extra)
    return rows[0] if rows else {}


def _first_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = as_float(payload.get(key))
        if number is not None:
            return number
    return None


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
