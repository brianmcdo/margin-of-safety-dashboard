"""Optional Yahoo close history. The historical screen does not call this unless asked."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from src.data.universe import normalize_ticker

logger = logging.getLogger(__name__)


def fetch_closes(tickers: list[str], start: date, end: date) -> dict[str, list[tuple[date, float]]]:
    """Daily closes from ``start`` through ``end``, inclusive.

    An empty input or a failed download returns no bars. The caller stores
    whatever came back and the screen still runs on prices already on disk.
    """
    symbols = [normalize_ticker(ticker) for ticker in tickers if ticker.strip()]
    symbols = list(dict.fromkeys(symbols))
    if not symbols or end < start:
        return {}
    try:
        import yfinance as yf

        frame = yf.download(
            symbols if len(symbols) > 1 else symbols[0],
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        logger.exception("Price history download failed for %s names", len(symbols))
        return {}
    if frame is None or getattr(frame, "empty", True):
        logger.warning("Price history download returned no rows")
        return {}
    return closes_from_frame(frame, symbols)


def closes_from_frame(frame: pd.DataFrame, symbols: list[str]) -> dict[str, list[tuple[date, float]]]:
    """Read a Close column from a yfinance download frame."""
    if frame.empty or "Close" not in _field_names(frame):
        return {}
    closes = frame["Close"]
    parsed: dict[str, list[tuple[date, float]]] = {}
    if isinstance(closes, pd.Series):
        symbol = symbols[0] if symbols else "UNKNOWN"
        bars = _bars(closes)
        if bars:
            parsed[symbol] = bars
        return parsed
    for column in closes.columns:
        bars = _bars(closes[column])
        if bars:
            parsed[str(column)] = bars
    return parsed


def _field_names(frame: pd.DataFrame) -> set[str]:
    if isinstance(frame.columns, pd.MultiIndex):
        return {str(name) for name in frame.columns.get_level_values(0)}
    return {str(name) for name in frame.columns}


def _bars(series: pd.Series) -> list[tuple[date, float]]:
    bars: list[tuple[date, float]] = []
    for stamp, value in series.items():
        if pd.isna(value):
            continue
        close = float(value)
        if close <= 0:
            continue
        bars.append((_as_date(stamp), close))
    return bars


def _as_date(stamp: object) -> date:
    if isinstance(stamp, date) and not hasattr(stamp, "hour"):
        return stamp
    return pd.Timestamp(stamp).date()
