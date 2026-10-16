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
    quality_statement_fields,
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


def resolve_fmp_api_key(settings: Settings) -> str:
    """Financial Modeling Prep key from the environment, then from config.yaml.

    The environment variable named by ``data.fmp_api_key_env`` wins so a key
    in the shell is not overwritten by a blank or stale config value. A value
    in ``data.fmp_api_key`` is used only when that variable is unset or empty.
    The key itself is never logged.
    """
    env_name = (settings.data.fmp_api_key_env or "FMP_API_KEY").strip()
    from_env = os.environ.get(env_name, "").strip() if env_name else ""
    if from_env:
        return from_env
    return settings.data.fmp_api_key.strip()


def resolve_source(settings: Settings) -> str:
    """Pick yfinance or FMP. Missing FMP credentials fall back to yfinance."""
    source = settings.data.source.strip().lower()
    has_fmp_key = bool(resolve_fmp_api_key(settings))
    if source == "auto":
        chosen = "fmp" if has_fmp_key else "yfinance"
        logger.info("Data source auto-selected: %s", chosen)
        return chosen
    if source == "fmp" and not has_fmp_key:
        logger.warning(
            "Financial Modeling Prep was requested but no API key is set "
            "in $%s or data.fmp_api_key; falling back to yfinance",
            settings.data.fmp_api_key_env or "FMP_API_KEY",
        )
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
        configured = [normalize_ticker(str(ticker)) for ticker in settings.universe.sample if str(ticker).strip()]
        return "sample", _dedupe(configured or list(SAMPLE_UNIVERSE))
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
        cached = store.get_meta(cache_key, ttl_hours=24)
        cached_value = as_float(cached)
        if cached_value is not None and cached_value > 0:
            logger.info("AAA corporate yield from cache: %.2f%%", cached_value)
            return cached_value
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={settings.fred.series}"
    yield_percent = _fetch_fred_yield(url, settings)
    if yield_percent is None:
        logger.warning(
            "FRED series %s is unavailable; using configured AAA yield %.2f%%",
            settings.fred.series,
            settings.fred.fallback_aaa_yield,
        )
        return settings.fred.fallback_aaa_yield
    if store is not None:
        store.set_meta(cache_key, f"{yield_percent:.6f}")
    logger.info("AAA corporate yield from FRED %s: %.2f%%", settings.fred.series, yield_percent)
    return yield_percent


def _fetch_fred_yield(url: str, settings: Settings) -> float | None:
    """Download one FRED CSV with retries. Returns None when every attempt fails."""
    attempts = max(1, settings.data.max_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            # A short timeout: a missing yield should fall back, not stall the scan.
            text = _http_get(url, min(8.0, settings.data.request_timeout_seconds))
            return _last_fred_value(text)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "FRED attempt %s/%s failed: %s",
                attempt,
                attempts,
                _redact(str(exc)),
            )
            if attempt < attempts:
                _sleep(_backoff_seconds(attempt, _is_rate_limit(exc), settings))
    logger.error("FRED request failed after %s attempt(s): %s", attempts, _redact(str(last_error)))
    return None


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
    logger.info(
        "Fundamentals cache: %s ticker(s) fresh within %.0f hours, %s to fetch from %s",
        len(fresh),
        ttl,
        len(needed),
        source,
    )
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
                stale = store.get_fundamentals(ticker, ttl, allow_expired=True)
                if stale is not None:
                    note = (
                        f"live fetch failed ({message}); "
                        f"used cached snapshot from {stale.fetched_at.isoformat()}"
                    )
                    logger.warning("%s: %s", ticker, note)
                    fresh.append(stale.model_copy(update={"notes": [*stale.notes, note]}))
                else:
                    logger.error(
                        "Failed to fetch %s and no cached snapshot is available: %s",
                        ticker,
                        message,
                    )
                    failures.append(FetchFailure(ticker=ticker, error=message))
            else:
                _log_data_gaps(stock)
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
    """Fetch one ticker, retrying rate limits, then trying the other source."""
    try:
        return _attempt_source(ticker, settings, source)
    except Exception as primary_error:
        alternate = _alternate_source(source, settings)
        if alternate is None:
            raise
        logger.warning(
            "%s failed from %s (%s); falling back to %s",
            ticker,
            source,
            _redact(str(primary_error)),
            alternate,
        )
        try:
            return _attempt_source(ticker, settings, alternate)
        except Exception as alternate_error:
            logger.error(
                "%s fallback %s also failed: %s",
                ticker,
                alternate,
                _redact(str(alternate_error)),
            )
            raise RuntimeError(
                f"{source} failed ({primary_error}); {alternate} failed ({alternate_error})"
            ) from alternate_error


def _attempt_source(ticker: str, settings: Settings, source: str) -> StockFundamentals:
    attempts = max(1, settings.data.max_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if source == "fmp":
                stock = fetch_fmp(ticker, settings)
            else:
                stock = fetch_yfinance(ticker)
            if settings.news.enabled:
                from src.news.sentiment import attach_headlines

                stock = attach_headlines(stock, settings.news)
            return stock
        except Exception as exc:
            last_error = exc
            rate_limited = _is_rate_limit(exc)
            logger.warning(
                "%s %s attempt %s/%s failed%s: %s",
                source,
                ticker,
                attempt,
                attempts,
                " (rate limit)" if rate_limited else "",
                _redact(str(exc)),
            )
            if attempt < attempts:
                delay = _backoff_seconds(attempt, rate_limited, settings)
                logger.info("Retrying %s in %.1fs", ticker, delay)
                _sleep(delay)
    if last_error is None:
        raise RuntimeError(f"no fetch attempt ran for {ticker}")
    raise last_error


def _alternate_source(source: str, settings: Settings) -> str | None:
    if source == "yfinance" and resolve_fmp_api_key(settings):
        return "fmp"
    if source == "fmp":
        return "yfinance"
    return None


def _is_rate_limit(exc: BaseException) -> bool:
    name = exc.__class__.__name__.lower().replace("_", "")
    if "ratelimit" in name:
        return True
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def _backoff_seconds(attempt: int, rate_limited: bool, settings: Settings) -> float:
    base = (
        settings.data.rate_limit_backoff_seconds
        if rate_limited
        else settings.data.retry_backoff_seconds
    )
    return max(0.0, base) * (2 ** max(0, attempt - 1))


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


_DATA_GAPS = (
    ("trailing_eps", "EPS"),
    ("book_value_per_share", "book value per share"),
    ("free_cash_flow", "free cash flow"),
    ("current_assets", "current assets"),
    ("total_liabilities", "total liabilities"),
    ("shares_outstanding", "shares outstanding"),
)


def _log_data_gaps(stock: StockFundamentals) -> None:
    if stock.notes:
        logger.warning("%s data notes: %s", stock.ticker, "; ".join(stock.notes))
    missing = [label for attr, label in _DATA_GAPS if getattr(stock, attr) is None]
    if missing:
        logger.warning("%s is missing %s", stock.ticker, ", ".join(missing))


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
        if _is_rate_limit(exc):
            raise
        notes.append(f"quote info unavailable: {exc.__class__.__name__}")

    price = info_number(info, "currentPrice", "regularMarketPrice")
    if price is None:
        try:
            fast = client.fast_info
            price = as_float(fast.get("lastPrice") if hasattr(fast, "get") else None)
            if price is None:
                price = as_float(getattr(fast, "last_price", None))
        except Exception as exc:
            if _is_rate_limit(exc):
                raise
            price = None
    if price is None:
        try:
            history = client.history(period="5d")
            if history is not None and not history.empty and "Close" in history.columns:
                price = as_float(history["Close"].iloc[-1])
                notes.append("price taken from recent daily history")
        except Exception as exc:
            if _is_rate_limit(exc):
                raise
            price = None
    if price is None or price <= 0:
        raise RuntimeError(f"no price returned for {symbol}")

    balance = _safe_frame(lambda: client.balance_sheet, notes, "balance sheet", symbol)
    income = _safe_frame(lambda: client.financials, notes, "income statement", symbol)
    cashflow = _safe_frame(lambda: client.cashflow, notes, "cash flow statement", symbol)
    fcf_history = _free_cash_flow_history(cashflow)
    total_equity = latest_line_item(balance, LINE_ITEMS["equity"])
    shares = info_number(info, "sharesOutstanding", "impliedSharesOutstanding")
    if shares is None:
        shares = latest_line_item(balance, LINE_ITEMS["shares"])
    free_cash_flow = info_number(info, "freeCashflow")
    if free_cash_flow is None and fcf_history:
        free_cash_flow = fcf_history[0]
        notes.append("free cash flow fell back to the latest annual figure")
    quality_fields = quality_statement_fields(balance, income, cashflow)
    if total_equity is None:
        total_equity = quality_fields.get("total_equity")
    quality_fields.pop("total_equity", None)

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
        **quality_fields,
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


def _safe_frame(
    loader: Callable[[], Any],
    notes: list[str],
    label: str,
    ticker: str,
) -> pd.DataFrame | None:
    try:
        frame = loader()
    except Exception as exc:
        if _is_rate_limit(exc):
            raise
        message = f"{label} unavailable: {exc.__class__.__name__}"
        notes.append(message)
        logger.warning("%s %s", ticker, message)
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
    """Optional Financial Modeling Prep fetch.

    The key comes from the environment variable named in config, or from
    ``data.fmp_api_key`` when that variable is empty.
    """
    symbol = normalize_ticker(ticker)
    api_key = resolve_fmp_api_key(settings)
    if not api_key:
        env_name = settings.data.fmp_api_key_env or "FMP_API_KEY"
        raise RuntimeError(f"No Financial Modeling Prep API key in ${env_name} or data.fmp_api_key")
    timeout = settings.data.request_timeout_seconds
    profile = _fmp_first(symbol, "profile", api_key, timeout)
    metrics = _fmp_first(symbol, "key-metrics-ttm", api_key, timeout)
    balance_rows = _fmp_list(symbol, "balance-sheet-statement", api_key, timeout, {"limit": 4})
    income_rows = _fmp_list(symbol, "income-statement", api_key, timeout, {"limit": 4})
    cash_rows = _fmp_list(symbol, "cash-flow-statement", api_key, timeout, {"limit": 4})
    balance = balance_rows[0] if balance_rows else {}
    income = income_rows[0] if income_rows else {}
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
        net_income=_first_number(income, "netIncome"),
        total_assets=_first_number(balance, "totalAssets"),
        retained_earnings=_first_number(balance, "retainedEarnings"),
        operating_cash_flow=_column_first(cash_rows, "operatingCashFlow"),
        gross_profit=_first_number(income, "grossProfit"),
        long_term_debt=_first_number(balance, "longTermDebt"),
        net_income_history=_column(income_rows, "netIncome"),
        total_assets_history=_column(balance_rows, "totalAssets"),
        operating_cash_flow_history=_column(cash_rows, "operatingCashFlow"),
        revenue_history=_column(income_rows, "revenue"),
        ebit_history=_column(income_rows, "ebit", "operatingIncome"),
        gross_profit_history=_column(income_rows, "grossProfit"),
        long_term_debt_history=_column(balance_rows, "longTermDebt"),
        total_debt_history=_column(balance_rows, "totalDebt"),
        cash_history=_column(balance_rows, "cashAndCashEquivalents", "cashAndShortTermInvestments"),
        equity_history=_column(balance_rows, "totalStockholdersEquity"),
        shares_history=_column(income_rows, "weightedAverageShsOutDil"),
        current_ratio_history=_ratio_history(
            _column(balance_rows, "totalCurrentAssets"),
            _column(balance_rows, "totalCurrentLiabilities"),
        ),
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
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "").strip()
        detail = f", retry after {retry_after}s" if retry_after else ""
        raise RuntimeError(f"FMP rate limit (429){detail}")
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        message = str(payload.get("Error Message") or payload.get("error") or "unexpected FMP payload")
        if "limit" in message.lower():
            raise RuntimeError(f"FMP rate limit: {message}")
        raise RuntimeError(message)
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


def _ratio_history(numerators: list[float | None], denominators: list[float | None]) -> list[float | None]:
    ratios: list[float | None] = []
    for numerator, denominator in zip(numerators, denominators):
        if numerator is None or denominator is None or denominator <= 0:
            ratios.append(None)
        else:
            ratios.append(numerator / denominator)
    return ratios


def _column(rows: list[dict[str, Any]], *keys: str) -> list[float | None]:
    values: list[float | None] = []
    for row in rows:
        number = None
        for key in keys:
            number = as_float(row.get(key))
            if number is not None:
                break
        values.append(number)
    return values


def _column_first(rows: list[dict[str, Any]], *keys: str) -> float | None:
    for value in _column(rows, *keys):
        if value is not None:
            return value
    return None


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
