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
    "total_assets": ("Total Assets",),
    "net_income": ("Net Income", "Net Income Common Stockholders"),
    "retained_earnings": ("Retained Earnings",),
    "gross_profit": ("Gross Profit",),
    "long_term_debt": (
        "Long Term Debt And Capital Lease Obligation",
        "Long Term Debt",
    ),
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


def annual_column_values(frame: pd.DataFrame | None, names: tuple[str, ...] | list[str]) -> list[float | None]:
    """One value per annual column, most recent first. Missing years stay None.

    Keeping the blank years in place lets two lines from the same statement
    be compared year by year. Dropping blanks would pair this year with an
    older year.
    """
    statement = normalize_statement(frame)
    if statement is None:
        return []
    index = {str(label).strip().lower(): label for label in statement.index}
    label = next((index.get(name.strip().lower()) for name in names if name.strip().lower() in index), None)
    if label is None:
        return []
    series = statement.loc[label]
    values = series.tolist() if hasattr(series, "tolist") else [series]
    return [as_float(value) for value in values]


def quality_statement_fields(
    balance: pd.DataFrame | None,
    income: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
) -> dict[str, float | None | list[float | None]]:
    """Annual lines used by the F-score, Altman Z, and return checks."""

    def columns(frame: pd.DataFrame | None, key: str) -> list[float | None]:
        return annual_column_values(frame, LINE_ITEMS[key])

    def first(values: list[float | None]) -> float | None:
        for value in values:
            if value is not None:
                return value
        return None

    assets = columns(balance, "total_assets")
    equity = columns(balance, "equity")
    current_assets = columns(balance, "current_assets")
    current_liabilities = columns(balance, "current_liabilities")
    ratios: list[float | None] = []
    for asset_value, liability_value in zip(current_assets, current_liabilities):
        if asset_value is None or liability_value is None or liability_value <= 0:
            ratios.append(None)
        else:
            ratios.append(asset_value / liability_value)
    return {
        "total_assets": first(assets),
        "total_assets_history": assets,
        "total_equity": first(equity),
        "equity_history": equity,
        "retained_earnings": first(columns(balance, "retained_earnings")),
        "long_term_debt": first(columns(balance, "long_term_debt")),
        "long_term_debt_history": columns(balance, "long_term_debt"),
        "total_debt_history": columns(balance, "total_debt"),
        "cash_history": columns(balance, "cash"),
        "current_ratio_history": ratios,
        "shares_history": columns(balance, "shares"),
        "net_income": first(columns(income, "net_income")),
        "net_income_history": columns(income, "net_income"),
        "revenue_history": columns(income, "revenue"),
        "ebit_history": columns(income, "ebit"),
        "gross_profit": first(columns(income, "gross_profit")),
        "gross_profit_history": columns(income, "gross_profit"),
        "operating_cash_flow": first(columns(cashflow, "operating_cash_flow")),
        "operating_cash_flow_history": columns(cashflow, "operating_cash_flow"),
    }


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
    net_income: float | None = None,
    total_assets: float | None = None,
    retained_earnings: float | None = None,
    operating_cash_flow: float | None = None,
    gross_profit: float | None = None,
    long_term_debt: float | None = None,
    net_income_history: list[float | None] | None = None,
    total_assets_history: list[float | None] | None = None,
    operating_cash_flow_history: list[float | None] | None = None,
    revenue_history: list[float | None] | None = None,
    ebit_history: list[float | None] | None = None,
    gross_profit_history: list[float | None] | None = None,
    long_term_debt_history: list[float | None] | None = None,
    total_debt_history: list[float | None] | None = None,
    cash_history: list[float | None] | None = None,
    equity_history: list[float | None] | None = None,
    current_ratio_history: list[float | None] | None = None,
    shares_history: list[float | None] | None = None,
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
        total_equity=equity,
        net_income=as_float(net_income),
        total_assets=as_float(total_assets),
        retained_earnings=as_float(retained_earnings),
        operating_cash_flow=as_float(operating_cash_flow),
        gross_profit=as_float(gross_profit),
        long_term_debt=as_float(long_term_debt),
        net_income_history=_aligned(net_income_history),
        total_assets_history=_aligned(total_assets_history),
        operating_cash_flow_history=_aligned(operating_cash_flow_history),
        revenue_history=_aligned(revenue_history),
        ebit_history=_aligned(ebit_history),
        gross_profit_history=_aligned(gross_profit_history),
        long_term_debt_history=_aligned(long_term_debt_history),
        total_debt_history=_aligned(total_debt_history),
        cash_history=_aligned(cash_history),
        equity_history=_aligned(equity_history),
        current_ratio_history=_aligned(current_ratio_history),
        shares_history=_aligned(shares_history),
        source=source,
        fetched_at=utcnow(),
        notes=notes,
    )


def _aligned(values: list[float | None] | None) -> list[float | None]:
    return [as_float(value) for value in (values or [])]


def info_number(info: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = as_float(info.get(key))
        if number is not None:
            return number
    return None
