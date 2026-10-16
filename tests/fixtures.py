"""Offline fundamentals. Tests use these instead of Yahoo or Financial Modeling Prep."""

from __future__ import annotations

from src.data.models import StockFundamentals


def _base(**overrides) -> StockFundamentals:
    payload = dict(
        ticker="VAL",
        name="Value Co",
        sector="Industrials",
        industry="Specialty Industrial Machinery",
        price=40.0,
        market_cap=800_000_000.0,
        shares_outstanding=20_000_000.0,
        trailing_eps=5.0,
        book_value_per_share=50.0,
        current_assets=400_000_000.0,
        total_liabilities=200_000_000.0,
        total_debt=100_000_000.0,
        cash=50_000_000.0,
        free_cash_flow=100_000_000.0,
        fcf_history=[90_000_000.0, 100_000_000.0, 110_000_000.0],
        revenue=500_000_000.0,
        ebit=200_000_000.0,
        current_ratio=2.5,
        debt_to_equity=0.10,
        average_volume=1_000_000.0,
    )
    payload.update(overrides)
    return StockFundamentals(**payload)


def healthy_stock(**overrides) -> StockFundamentals:
    """A solvent industrial name with positive earnings, book value, and cash flow."""
    return _base(**overrides)


def negative_earnings_stock() -> StockFundamentals:
    """Loss-making operations. Graham methods should abstain; the DCF can still run."""
    return _base(
        ticker="LOSS",
        name="Loss Co",
        trailing_eps=-3.0,
        ebit=-20_000_000.0,
        free_cash_flow=40_000_000.0,
        fcf_history=[30_000_000.0, 40_000_000.0, 50_000_000.0],
    )


def missing_data_stock() -> StockFundamentals:
    """A price and a share count, with the statements left blank."""
    return StockFundamentals(
        ticker="MISS",
        name="Missing Co",
        price=25.0,
        shares_outstanding=1_000_000.0,
        market_cap=25_000_000.0,
    )


def high_debt_stock() -> StockFundamentals:
    """Debt well above the default one-times-equity ceiling."""
    return _base(
        ticker="DEBT",
        name="Levered Co",
        total_debt=2_000_000_000.0,
        debt_to_equity=8.0,
        current_ratio=2.5,
    )


def zero_book_stock() -> StockFundamentals:
    """Book value per share is zero, so the Graham Number and P/B abstain."""
    return _base(
        ticker="BOOK",
        name="Zero Book Co",
        book_value_per_share=0.0,
    )


def sample_universe() -> list[StockFundamentals]:
    """The five cases above, each as a new model instance."""
    return [
        healthy_stock(),
        negative_earnings_stock(),
        missing_data_stock(),
        high_debt_stock(),
        zero_book_stock(),
    ]
