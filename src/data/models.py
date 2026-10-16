"""Typed records shared by the data, valuation, and signal layers."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_float(value: Any) -> float | None:
    """Convert a raw vendor value into a finite float, or return None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


class NewsHeadline(BaseModel):
    title: str
    published_at: datetime | None = None


class StockFundamentals(BaseModel):
    """Point-in-time fundamentals for one equity.

    Free cash flow is treated as equity free cash flow (cash from operations
    minus capital expenditure). Discounting it produces an equity value, so
    net debt is not subtracted a second time.
    """

    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    price: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    trailing_eps: float | None = None
    book_value_per_share: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    total_liabilities: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    inventory: float | None = None
    receivables: float | None = None
    free_cash_flow: float | None = None
    fcf_history: list[float] = Field(default_factory=list)
    revenue: float | None = None
    ebit: float | None = None
    current_ratio: float | None = None
    debt_to_equity: float | None = None
    average_volume: float | None = None
    enterprise_value: float | None = None
    total_equity: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    retained_earnings: float | None = None
    operating_cash_flow: float | None = None
    gross_profit: float | None = None
    long_term_debt: float | None = None
    net_income_history: list[float | None] = Field(default_factory=list)
    total_assets_history: list[float | None] = Field(default_factory=list)
    operating_cash_flow_history: list[float | None] = Field(default_factory=list)
    revenue_history: list[float | None] = Field(default_factory=list)
    ebit_history: list[float | None] = Field(default_factory=list)
    gross_profit_history: list[float | None] = Field(default_factory=list)
    long_term_debt_history: list[float | None] = Field(default_factory=list)
    total_debt_history: list[float | None] = Field(default_factory=list)
    cash_history: list[float | None] = Field(default_factory=list)
    equity_history: list[float | None] = Field(default_factory=list)
    current_ratio_history: list[float | None] = Field(default_factory=list)
    shares_history: list[float | None] = Field(default_factory=list)
    source: str = "yfinance"
    fetched_at: datetime = Field(default_factory=utcnow)
    notes: list[str] = Field(default_factory=list)
    headlines: list[NewsHeadline] = Field(default_factory=list)


class MethodEstimate(BaseModel):
    method: str
    intrinsic_value_per_share: float | None = None
    margin_of_safety: float | None = None
    note: str = ""


class SensitivityCell(BaseModel):
    price_multiplier: float
    discount_rate: float
    value_per_share: float | None = None
    commodity_price: float | None = None


class SignalRecord(BaseModel):
    """One ranked row: valuation, score, screen result, and the signal label."""

    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    signal: str
    price: float | None = None
    graham_number: float | None = None
    graham_formula: float | None = None
    dcf_value: float | None = None
    dcf_low: float | None = None
    dcf_high: float | None = None
    ncav_per_share: float | None = None
    ncav_strict_per_share: float | None = None
    margin_of_safety: float | None = None
    mos_by_method: dict[str, float | None] = Field(default_factory=dict)
    composite_score: float = 0.0
    quality_score: float | None = None
    f_score: int | None = None
    f_score_available: int = 0
    altman_z: float | None = None
    bankruptcy_risk: str = "unknown"
    roic: float | None = None
    roe: float | None = None
    low_returns: bool = False
    negative_news: bool = False
    news_headlines: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    passed_screen: bool = False
    screen_failures: list[str] = Field(default_factory=list)
    is_commodity_producer: bool = False
    commodity_name: str | None = None
    commodity_unit: str | None = None
    commodity_nav: float | None = None
    low_cost_producer: bool = False
    is_financial: bool = False
    methods: list[MethodEstimate] = Field(default_factory=list)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    sensitivity: list[SensitivityCell] = Field(default_factory=list)
    aaa_yield: float | None = None


class FetchFailure(BaseModel):
    ticker: str
    error: str


class PipelineResult(BaseModel):
    run_id: str
    created_at: datetime
    universe: str
    records: list[SignalRecord]
    failures: list[FetchFailure] = Field(default_factory=list)
    aaa_yield: float
    new_strong_value: list[str] = Field(default_factory=list)
