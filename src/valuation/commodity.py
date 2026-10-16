"""Energy and mining producers: detection, a cash-flow NAV, and a price grid.

The NAV scales normalized free cash flow by a planning price divided by a
reference price, then runs the same conservative DCF used for every other
name. It is not a reserve NAV. Reserves, royalties, hedges, and the cost
curve are not in the data.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config import CommodityQuote, CommoditySettings, DcfSettings
from src.data.models import SensitivityCell, StockFundamentals
from src.valuation.dcf import conservative_dcf_per_share, normalized_free_cash_flow

# Longer industry phrases are tested first so "oil & gas" wins over "oil".
_COMMODITY_FOR_KEYWORD = (
    ("oil & gas", "oil"),
    ("gas e&p", "oil"),
    ("oil", "oil"),
    ("gold", "gold"),
    ("silver", "gold"),
    ("copper", "copper"),
    ("uranium", "metals"),
    ("lithium", "metals"),
    ("coal", "metals"),
    ("iron", "metals"),
    ("steel", "metals"),
    ("aluminum", "metals"),
    ("aluminium", "metals"),
    ("mining", "metals"),
    ("metals", "metals"),
)


@dataclass(frozen=True)
class ProducerProfile:
    """Which planning price applies, and whether sector or industry matched."""

    commodity: str
    unit: str
    matched: str


def classify_producer(stock: StockFundamentals, settings: CommoditySettings) -> ProducerProfile | None:
    """Energy and mining names, by sector or by industry. Utilities and banks are out.

    An industry keyword chooses oil, gold, copper, or the metals index.
    A sector match with no mineral in the industry name uses oil for Energy
    and the metals index for materials.
    """
    if not settings.enabled:
        return None
    sector = (stock.sector or "").lower()
    industry = (stock.industry or "").lower()
    if "utility" in sector or "utilities" in sector or "utility" in industry:
        return None
    if "financial" in sector:
        return None
    commodity = _commodity_from_industry(industry, settings.industry_keywords)
    matched = "industry"
    if commodity is None and _sector_matches(sector, settings.sector_keywords):
        commodity = "oil" if "energy" in sector else "metals"
        matched = "sector"
    if commodity is None:
        return None
    quote = settings.prices.get(commodity)
    unit = quote.unit if quote is not None else ""
    return ProducerProfile(commodity=commodity, unit=unit, matched=matched)


def is_commodity_producer(stock: StockFundamentals, settings: CommoditySettings) -> bool:
    """True when ``classify_producer`` finds an energy or mining business."""
    return classify_producer(stock, settings) is not None


def is_low_cost_producer(
    *,
    is_producer: bool,
    quality_score: float | None,
    low_returns: bool,
    bankruptcy_risk: str,
    free_cash_flow: float | None,
    debt_to_equity: float | None,
    settings: CommoditySettings,
) -> bool:
    """High quality, positive free cash flow, and modest debt.

    This is a survival screen for a price downturn. It is not an all-in
    sustaining cost and it does not rank the cost curve.
    """
    if not is_producer or quality_score is None:
        return False
    if quality_score < settings.low_cost_min_quality or low_returns:
        return False
    if bankruptcy_risk == "distress":
        return False
    if free_cash_flow is None or free_cash_flow <= 0:
        return False
    if debt_to_equity is None or debt_to_equity > settings.low_cost_max_debt_equity:
        return False
    return True


def cash_flow_scale(price: float | None, reference: float | None) -> float | None:
    """Planning price divided by the price embedded in trailing cash flow."""
    if price is None or reference is None or reference <= 0 or price < 0:
        return None
    return price / reference


def quotes_with_overrides(
    settings: CommoditySettings,
    normalized_overrides: dict[str, float] | None = None,
) -> dict[str, CommodityQuote]:
    """Copy the configured quotes and replace planning prices the user edited."""
    quotes = {name: quote.model_copy() for name, quote in settings.prices.items()}
    for name, normalized in (normalized_overrides or {}).items():
        if name in quotes and normalized is not None:
            quotes[name] = quotes[name].model_copy(update={"normalized": float(normalized)})
    return quotes


def commodity_nav_per_share(
    stock: StockFundamentals,
    commodity: CommoditySettings,
    dcf: DcfSettings,
    *,
    normalized_overrides: dict[str, float] | None = None,
) -> tuple[float | None, str]:
    """DCF per share at the planning price. The headline DCF is left unchanged."""
    profile = classify_producer(stock, commodity)
    if profile is None:
        return None, "Not an energy or mining producer"
    quote = quotes_with_overrides(commodity, normalized_overrides).get(profile.commodity)
    if quote is None:
        return None, f"No planning price configured for {profile.commodity}"
    scale = cash_flow_scale(quote.normalized, quote.reference)
    base_fcf, cash_note = normalized_free_cash_flow(
        stock.free_cash_flow,
        stock.fcf_history,
        dcf.normalize_years,
    )
    if scale is None or base_fcf is None:
        return None, cash_note or "Commodity NAV needs a planning price and free cash flow"
    value, detail = conservative_dcf_per_share(
        base_fcf * scale,
        stock.shares_outstanding,
        growth_rate=dcf.growth_rate,
        years=dcf.years,
        discount_rate=dcf.discount_rate,
        terminal_growth=dcf.terminal_growth,
        max_terminal_share=dcf.max_terminal_share,
    )
    note = (
        f"{profile.commodity} planning price {quote.normalized:g} {quote.unit}; "
        f"trailing cash flow assumed at {quote.reference:g}; "
        f"cash flow scaled by {scale:.2f}. {detail}"
    )
    return value, note


def commodity_sensitivity(
    stock: StockFundamentals,
    commodity: CommoditySettings,
    dcf: DcfSettings,
    *,
    normalized_overrides: dict[str, float] | None = None,
) -> list[SensitivityCell]:
    """Value per share at each planning-price multiple and discount rate.

    A multiple of 1.00 is the user's planning price, not the trailing price.
    Each row's commodity price is planning price times that multiple.
    """
    profile = classify_producer(stock, commodity)
    quotes = quotes_with_overrides(commodity, normalized_overrides)
    quote = None if profile is None else quotes.get(profile.commodity)
    base_fcf, _note = normalized_free_cash_flow(
        stock.free_cash_flow,
        stock.fcf_history,
        dcf.normalize_years,
    )
    cells: list[SensitivityCell] = []
    for multiplier in commodity.sensitivity_price_multipliers:
        price = None if quote is None else quote.normalized * multiplier
        reference = None if quote is None else quote.reference
        scale = cash_flow_scale(price, reference)
        if scale is None and quote is None:
            scale = multiplier
        scaled = None if base_fcf is None or scale is None else base_fcf * scale
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
                    commodity_price=price,
                )
            )
    return cells


def assumption_text(settings: CommoditySettings) -> str:
    """The assumptions shown next to the sensitivity table and in the README."""
    oil = settings.prices.get("oil")
    gold = settings.prices.get("gold")
    copper = settings.prices.get("copper")
    return (
        "Commodity NAV scales median free cash flow by planning price / reference price, "
        "then discounts it with the same conservative DCF used elsewhere. "
        f"Defaults: oil { _px(oil) }, gold { _px(gold) }, copper { _px(copper) }. "
        "The reference price is the price assumed to sit inside trailing cash flow. "
        "The 1.00 row of the table is the planning price, and the other rows multiply it. "
        "This is not a reserve NAV: reserves, royalties, hedges, and the cost curve are omitted. "
        f"A low-cost flag requires quality of at least {settings.low_cost_min_quality:.0f}, "
        f"positive free cash flow, debt/equity at or below {settings.low_cost_max_debt_equity:.2f}, "
        "and no distress or persistently low returns. It is not an all-in sustaining cost. "
        "The commodity NAV does not enter the composite score."
    )


def _commodity_from_industry(industry: str, keywords: list[str]) -> str | None:
    ordered = sorted((keyword.lower() for keyword in keywords), key=len, reverse=True)
    known = dict(_COMMODITY_FOR_KEYWORD)
    for keyword in ordered:
        if keyword and keyword in industry:
            return known.get(keyword, "metals")
    return None


def _sector_matches(sector: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in sector for keyword in keywords if keyword)


def _px(quote: CommodityQuote | None) -> str:
    if quote is None:
        return "unset"
    return f"{quote.normalized:g} versus reference {quote.reference:g} {quote.unit}"
