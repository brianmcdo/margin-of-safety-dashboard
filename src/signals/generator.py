"""Signal labels, explanations, and tabular export."""

from __future__ import annotations

from src.config import Settings
from src.data.models import MethodEstimate, SignalRecord, StockFundamentals
from src.screening.filters import ScreenResult, metrics_dict
from src.valuation.commodity import commodity_sensitivity, is_commodity_producer
from src.valuation.dcf import conservative_dcf_per_share, normalized_free_cash_flow
from src.valuation.graham import graham_formula_value, graham_number
from src.valuation.scoring import composite_score, margin_of_safety, median_margin

STRONG = "Strong Value"
MODERATE = "Moderate Value"
WATCHLIST = "Watchlist"
DEEP = "Deep Value / Net-Net"
NONE = "No Signal"

SIGNAL_RANK = {
    STRONG: 0,
    DEEP: 1,
    MODERATE: 2,
    WATCHLIST: 3,
    NONE: 4,
}


def classify_signal(
    *,
    passed_screen: bool,
    liquidity_ok: bool,
    net_net: bool,
    earnings_support: bool,
    median_mos: float | None,
    min_method_mos: float | None,
    strong_threshold: float,
    moderate_threshold: float,
) -> str:
    """Choose one label.

    A net-net with no earnings support is Deep Value even when the ordinary
    earnings screen fails, provided the liquidity floors still hold. When
    earnings-based methods also clear the strong threshold and the screen
    passes, the label is Strong Value and the net-net fact is kept as a reason.
    Disagreement (one method at a premium while the median is high) downgrades
    Strong Value to Moderate Value.
    """
    disagreement = (
        median_mos is not None
        and median_mos >= strong_threshold
        and min_method_mos is not None
        and min_method_mos <= 0
    )
    if net_net and liquidity_ok and not earnings_support:
        return DEEP
    if passed_screen and median_mos is not None and median_mos >= strong_threshold and not disagreement:
        return STRONG
    if (
        passed_screen
        and median_mos is not None
        and median_mos >= strong_threshold
        and disagreement
    ):
        return MODERATE
    if passed_screen and median_mos is not None and median_mos >= moderate_threshold:
        return MODERATE
    if net_net and liquidity_ok:
        return DEEP
    if passed_screen and (median_mos is None or median_mos >= 0):
        return WATCHLIST
    return NONE


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def build_record(
    stock: StockFundamentals,
    screen: ScreenResult,
    settings: Settings,
    aaa_yield: float,
) -> SignalRecord:
    """Value one stock and attach its score, signal, and reasons."""
    price = stock.price
    valuation = settings.valuation
    graham = graham_number(stock.trailing_eps, stock.book_value_per_share)
    formula, formula_note = graham_formula_value(
        stock.trailing_eps,
        valuation.graham_growth_percent,
        aaa_yield,
        max_growth_percent=valuation.max_graham_growth_percent,
        max_implied_pe=valuation.max_graham_implied_pe,
    )
    base_fcf, fcf_note = normalized_free_cash_flow(
        stock.free_cash_flow,
        stock.fcf_history,
        valuation.dcf.normalize_years,
    )
    dcf_value, dcf_note = conservative_dcf_per_share(
        base_fcf,
        stock.shares_outstanding,
        growth_rate=valuation.dcf.growth_rate,
        years=valuation.dcf.years,
        discount_rate=valuation.dcf.discount_rate,
        terminal_growth=valuation.dcf.terminal_growth,
        max_terminal_share=valuation.dcf.max_terminal_share,
    )
    if screen.is_financial:
        dcf_note = f"{dcf_note}; free-cash-flow DCF is a weak fit for financials".strip("; ")

    ncav = None if screen.is_financial else screen.metrics.ncav_per_share
    ncav_strict = None if screen.is_financial else screen.metrics.ncav_strict_per_share
    estimates = [
        MethodEstimate(
            method="Graham Number",
            intrinsic_value_per_share=graham,
            margin_of_safety=margin_of_safety(graham, price),
            note="sqrt(22.5 × EPS × book value per share)",
        ),
        MethodEstimate(
            method="Graham Formula",
            intrinsic_value_per_share=formula,
            margin_of_safety=margin_of_safety(formula, price),
            note=formula_note or f"growth {valuation.graham_growth_percent:g}%, AAA yield {aaa_yield:.2f}%",
        ),
        MethodEstimate(
            method="DCF",
            intrinsic_value_per_share=dcf_value,
            margin_of_safety=margin_of_safety(dcf_value, price),
            note=f"{fcf_note}; {dcf_note}",
        ),
        MethodEstimate(
            method="NCAV",
            intrinsic_value_per_share=ncav,
            margin_of_safety=margin_of_safety(ncav, price),
            note=(
                "not used for financials"
                if screen.is_financial
                else "current assets minus total liabilities, per share"
            ),
        ),
    ]
    mos_values = [item.margin_of_safety for item in estimates if item.margin_of_safety is not None]
    # NCAV is a liquidation floor. It stays in the median so a single optimistic
    # cash-flow model cannot speak alone, but trading above liquidation value
    # is normal and is not treated as a disagreement among going-concern methods.
    median = median_margin(mos_values)
    earnings_mos = [
        item.margin_of_safety
        for item in estimates
        if item.method != "NCAV" and item.margin_of_safety is not None
    ]
    earnings_support = any(value >= valuation.mos.moderate for value in earnings_mos)
    minimum = min(earnings_mos) if earnings_mos else None
    signal = classify_signal(
        passed_screen=screen.passed,
        liquidity_ok=screen.liquidity_ok,
        net_net=screen.net_net,
        earnings_support=earnings_support,
        median_mos=median,
        min_method_mos=minimum,
        strong_threshold=valuation.mos.strong,
        moderate_threshold=valuation.mos.moderate,
    )
    score = composite_score(stock, screen.metrics, median, settings.scoring.weights)
    reasons = _reasons(stock, screen, estimates, median, signal, aaa_yield)
    commodity = is_commodity_producer(stock, settings.commodity)
    sensitivity = (
        commodity_sensitivity(stock, settings.commodity, settings.valuation.dcf)
        if commodity
        else []
    )
    return SignalRecord(
        ticker=stock.ticker,
        name=stock.name,
        sector=stock.sector,
        industry=stock.industry,
        signal=signal,
        price=price,
        graham_number=graham,
        graham_formula=formula,
        dcf_value=dcf_value,
        ncav_per_share=ncav,
        ncav_strict_per_share=ncav_strict,
        margin_of_safety=median,
        mos_by_method={item.method: item.margin_of_safety for item in estimates},
        composite_score=score,
        reasons=reasons,
        passed_screen=screen.passed,
        screen_failures=screen.failures,
        is_commodity_producer=commodity,
        is_financial=screen.is_financial,
        methods=estimates,
        metrics=metrics_dict(screen.metrics),
        sensitivity=sensitivity,
        aaa_yield=aaa_yield,
    )


def _reasons(
    stock: StockFundamentals,
    screen: ScreenResult,
    estimates: list[MethodEstimate],
    median: float | None,
    signal: str,
    aaa_yield: float,
) -> list[str]:
    metrics = screen.metrics
    reasons: list[str] = []
    usable = [item for item in estimates if item.margin_of_safety is not None]
    if median is not None:
        reasons.append(
            f"Median margin of safety is {_fmt_pct(median)} across {len(usable)} methods"
        )
    parts: list[str] = []
    if metrics.pe is not None:
        parts.append(f"P/E {metrics.pe:.1f}")
    if metrics.pb is not None:
        parts.append(f"P/B {metrics.pb:.2f}")
    if metrics.ev_ebit is not None:
        parts.append(f"EV/EBIT {metrics.ev_ebit:.1f}")
    if parts:
        reasons.append(", ".join(parts))
    balance: list[str] = []
    if metrics.debt_to_equity is not None:
        balance.append(f"debt/equity {metrics.debt_to_equity:.2f}")
    if metrics.current_ratio is not None:
        balance.append(f"current ratio {metrics.current_ratio:.2f}")
    if balance:
        reasons.append(", ".join(balance))
    if screen.net_net and metrics.ncav_per_share is not None and stock.price is not None:
        reasons.append(
            f"Price {_fmt_price(stock.price)} is below NCAV {_fmt_price(metrics.ncav_per_share)}"
        )
        if (
            metrics.ncav_strict_per_share is not None
            and stock.price < metrics.ncav_strict_per_share
        ):
            reasons.append(
                f"Also below strict NCAV {_fmt_price(metrics.ncav_strict_per_share)}"
            )
    if signal == WATCHLIST:
        reasons.append("Multiples pass the screen, but the margin of safety is still thin")
    if signal == MODERATE and any(
        item.margin_of_safety is not None and item.margin_of_safety <= 0 for item in usable
    ):
        reasons.append("Methods disagree: at least one intrinsic value is below the price")
    if screen.bypassed:
        reasons.append("Earnings and leverage filters were bypassed because price is below NCAV")
    if screen.is_financial:
        reasons.append("Financial company: NCAV and EV/EBIT are not used as primary evidence")
    if screen.failures and signal == NONE:
        reasons.append("Screen: " + "; ".join(screen.failures[:4]))
    reasons.append(f"AAA corporate yield used in the Graham formula: {aaa_yield:.2f}%")
    return reasons


def rank_records(records: list[SignalRecord], min_mos: float) -> list[SignalRecord]:
    """Primary ranking: signals that clear the margin-of-safety floor, by score."""

    def include(record: SignalRecord) -> bool:
        if record.signal == NONE:
            return False
        if record.signal == WATCHLIST:
            return False
        if record.margin_of_safety is None:
            return record.signal == DEEP
        return record.margin_of_safety >= min_mos

    ranked = [record for record in records if include(record)]
    ranked.sort(
        key=lambda record: (
            -record.composite_score,
            -(record.margin_of_safety if record.margin_of_safety is not None else -1),
            SIGNAL_RANK.get(record.signal, 9),
            record.ticker,
        )
    )
    return ranked


def records_to_rows(records: list[SignalRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in records:
        rows.append(
            {
                "ticker": record.ticker,
                "name": record.name,
                "signal": record.signal,
                "composite_score": record.composite_score,
                "margin_of_safety": record.margin_of_safety,
                "price": record.price,
                "graham_number": record.graham_number,
                "graham_formula": record.graham_formula,
                "dcf_value": record.dcf_value,
                "ncav_per_share": record.ncav_per_share,
                "ncav_strict_per_share": record.ncav_strict_per_share,
                "sector": record.sector,
                "industry": record.industry,
                "is_commodity_producer": record.is_commodity_producer,
                "is_financial": record.is_financial,
                "pe": record.metrics.get("pe"),
                "pb": record.metrics.get("pb"),
                "ev_ebit": record.metrics.get("ev_ebit"),
                "debt_to_equity": record.metrics.get("debt_to_equity"),
                "current_ratio": record.metrics.get("current_ratio"),
                "passed_screen": record.passed_screen,
                "screen_failures": " | ".join(record.screen_failures),
                "reasons": " | ".join(record.reasons),
                "mos_graham_number": record.mos_by_method.get("Graham Number"),
                "mos_graham_formula": record.mos_by_method.get("Graham Formula"),
                "mos_dcf": record.mos_by_method.get("DCF"),
                "mos_ncav": record.mos_by_method.get("NCAV"),
            }
        )
    return rows
