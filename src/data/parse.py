"""Turn vendor line items into a normalized fundamentals record."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.data.models import StockFundamentals, as_float, utcnow
from src.data.universe import normalize_ticker

LINE_ITEMS: dict[str, tuple[str, ...]] = {
    "current_assets": ("Current Assets", "Total Current Assets"),
    "current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
    "total_liabilities": (
        "Total Liabilities Net Minority Interest",
        "Total Liab",
        "Total Liabilities",
    ),
    "total_debt": ("Total Debt",),
    "cash": (
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Federal Funds Sold",
    ),
    "inventory": ("Inventory",),
    "receivables": ("Accounts Receivable", "Net Receivables", "Receivables"),
    "equity": (
        "Stockholders Equity",
        "Common Stock Equity",
        "Total Stockholder Equity",
        "Total Equity Gross Minority Interest",
    ),
    "shares": ("Ordinary Shares Number", "Share Issued", "Diluted Average Shares"),
    "ebit": ("EBIT", "Operating Income"),
    "revenue": ("Total Revenue",),
    "diluted_eps": ("Diluted EPS",),
    "basic_eps": ("Basic EPS",),
    "free_cash_flow": ("Free Cash Flow",),
    "operating_cash_flow": ("Operating Cash Flow", "Total Cash From Operating Activities"),
    "capital_expenditure": ("Capital Expenditure",),
}


def normalize_statement(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    """Return a statement with line items on the index and dates as columns."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.transpose()
    return frame


def latest_line_item(frame: pd.DataFrame | None, names: tuple[str, ...] | list[str]) -> float | None:
    """First finite value under the most recent column for any alias."""
    statement = normalize_statement(frame)
    if statement is None:
        return None
    index = {str(label).strip().lower(): label for label in statement.index}
    for name in names:
        label = index.get(name.strip().lower())
        if label is None:
            continue
        series = statement.loc[label]
        values = series.tolist() if hasattr(series, "tolist") else [series]
        for value in values:
            number = as_float(value)
            if number is not None:
                return number
    return None


def annual_series(frame: pd.DataFrame | None, names: tuple[str, ...] | list[str]) -> list[float]:
    """Annual values, most recent first, skipping blanks."""
    statement = normalize_statement(frame)
    if statement is None:
        return []
    index = {str(label).strip().lower(): label for label in statement.index}
    for name in names:
        label = index.get(name.strip().lower())
        if label is None:
            continue
        series = statement.loc[label]
        values = series.tolist() if hasattr(series, "tolist") else [series]
        return [number for value in values if (number := as_float(value)) is not None]
    return []


def coerce_debt_to_equity(
    reported: float | None,
    *,
    reported_is_percent: bool,
    total_debt: float | None,
    book_equity: float | None,
) -> float | None:
    """Prefer debt / book equity. Yahoo's info field is a percentage.

    A reported value of 150 from Yahoo means 1.50 turns of debt to equity,
    not a ratio of 150. Statement totals avoid that ambiguity and win when
    both are present.
    """
    debt = as_float(total_debt)
    equity = as_float(book_equity)
    if debt is not None and equity is not None and equity > 0 and debt >= 0:
        return debt / equity
    raw = as_float(reported)
    if raw is None:
        return None
    if reported_is_percent:
        return raw / 100.0
    return raw


def assemble_fundamentals(
    *,
    ticker: str,
    name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    price: float | None = None,
    market_cap: float | None = None,
    shares_outstanding: float | None = None,
    trailing_eps: float | None = None,
    book_value_per_share: float | None = None,
    total_equity: float | None = None,
    current_assets: float | None = None,
    current_liabilities: float | None = None,
    total_liabilities: float | None = None,
    total_debt: float | None = None,
    cash: float | None = None,
    inventory: float | None = None,
    receivables: float | None = None,
    free_cash_flow: float | None = None,
    fcf_history: list[float] | None = None,
    revenue: float | None = None,
    ebit: float | None = None,
    current_ratio: float | None = None,
    debt_to_equity_reported: float | None = None,
    debt_to_equity_is_percent: bool = False,
    average_volume: float | None = None,
    enterprise_value: float | None = None,
    source: str = "yfinance",
    notes: list[str] | None = None,
) -> StockFundamentals:
    """Normalize one vendor payload into the record the rest of the system uses."""
    notes = list(notes or [])
    shares = as_float(shares_outstanding)
    equity = as_float(total_equity)
    book = as_float(book_value_per_share)
    if book is None and equity is not None and shares is not None and shares > 0:
        book = equity / shares
        notes.append("book value per share derived from equity and shares")
    if shares is None and equity is not None and book is not None and book > 0:
        shares = equity / book
        notes.append("shares outstanding derived from equity and book value")

    price_value = as_float(price)
    market = as_float(market_cap)
    if market is None and price_value is not None and shares is not None and shares > 0:
        market = price_value * shares

    current_ratio_value = as_float(current_ratio)
    assets = as_float(current_assets)
    current_liab = as_float(current_liabilities)
    if (
        current_ratio_value is None
        and assets is not None
        and current_liab is not None
        and current_liab > 0
    ):
        current_ratio_value = assets / current_liab

    debt_to_equity = coerce_debt_to_equity(
        debt_to_equity_reported,
        reported_is_percent=debt_to_equity_is_percent,
        total_debt=as_float(total_debt),
        book_equity=equity if equity is not None else (
            book * shares if book is not None and shares is not None else None
        ),
    )
    history = [number for value in (fcf_history or []) if (number := as_float(value)) is not None]
    return StockFundamentals(
        ticker=normalize_ticker(ticker),
        name=name,
        sector=sector,
        industry=industry,
        price=price_value,
        market_cap=market,
        shares_outstanding=shares,
        trailing_eps=as_float(trailing_eps),
        book_value_per_share=book,
        current_assets=assets,
        current_liabilities=current_liab,
        total_liabilities=as_float(total_liabilities),
        total_debt=as_float(total_debt),
        cash=as_float(cash),
        inventory=as_float(inventory),
        receivables=as_float(receivables),
        free_cash_flow=as_float(free_cash_flow),
        fcf_history=history,
        revenue=as_float(revenue),
        ebit=as_float(ebit),
        current_ratio=current_ratio_value,
        debt_to_equity=debt_to_equity,
        average_volume=as_float(average_volume),
        enterprise_value=as_float(enterprise_value),
        source=source,
        fetched_at=utcnow(),
        notes=notes,
    )


def info_number(info: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = as_float(info.get(key))
        if number is not None:
            return number
    return None
