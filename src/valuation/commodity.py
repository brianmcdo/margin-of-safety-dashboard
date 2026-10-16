"""Commodity-producer flag and a price/discount sensitivity grid."""

from __future__ import annotations

from src.config import CommoditySettings, DcfSettings
from src.data.models import SensitivityCell, StockFundamentals
from src.valuation.dcf import conservative_dcf_per_share, normalized_free_cash_flow


def is_commodity_producer(stock: StockFundamentals, settings: CommoditySettings) -> bool:
    """True for energy and mining businesses, false for utilities and banks.

    Keyword matching is intentionally narrow. A gas utility or a chemical
    distributor should not inherit a commodity-price sensitivity table.
    """
    if not settings.enabled:
        return False
    sector = (stock.sector or "").lower()
    industry = (stock.industry or "").lower()
    if "utility" in sector or "utilities" in sector or "utility" in industry:
        return False
    if "financial" in sector:
        return False
    return any(keyword.lower() in industry for keyword in settings.industry_keywords)


def commodity_sensitivity(
    stock: StockFundamentals,
    commodity: CommoditySettings,
    dcf: DcfSettings,
) -> list[SensitivityCell]:
    """Revalue equity FCF under alternate commodity-price and discount assumptions.

    The price multiplier scales normalized free cash flow. A multiplier of
    0.70 asks what the equity is worth if mid-cycle cash flow is 30% lower
    than the cash flow used in the base case, which is the right question
    when today's commodity price is above a conservative planning price.
    This is a sensitivity, not a reserve-based NAV.
    """
    base_fcf, _note = normalized_free_cash_flow(
        stock.free_cash_flow,
        stock.fcf_history,
        dcf.normalize_years,
    )
    cells: list[SensitivityCell] = []
    for multiplier in commodity.sensitivity_price_multipliers:
        scaled = None if base_fcf is None else base_fcf * multiplier
        for discount in commodity.sensitivity_discount_rates:
            value, _detail = conservative_dcf_per_share(
                scaled,
                stock.shares_outstanding,
                growth_rate=dcf.growth_rate,
                years=dcf.years,
                discount_rate=discount,
                terminal_growth=dcf.terminal_growth,
                max_terminal_share=dcf.max_terminal_share,
            )
            cells.append(
                SensitivityCell(
                    price_multiplier=multiplier,
                    discount_rate=discount,
                    value_per_share=value,
                )
            )
    return cells
