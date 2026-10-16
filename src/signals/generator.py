"""Signal labels, explanations, and tabular export."""

from __future__ import annotations

from src.config import Settings
from src.data.models import MethodEstimate, SignalRecord, StockFundamentals
from src.screening.filters import ScreenResult, metrics_dict
from src.valuation.commodity import (
    classify_producer,
    commodity_nav_per_share,
    commodity_sensitivity,
    is_low_cost_producer,
)
from src.valuation.dcf import conservative_dcf_range
from src.valuation.graham import graham_formula_value, graham_number
from src.valuation.ncav import ncav_per_share
from src.valuation.quality import QualityAssessment, assess_quality
from src.news.sentiment import assess_news
from src.valuation.scoring import composite_score, margin_of_safety, median_margin

STRONG = "Strong Value"
MODERATE = "Moderate Value"
WATCHLIST = "Watchlist"
DEEP = "Deep Value / Net-Net"
AVOID = "Avoid"
NONE = AVOID

SIGNAL_RANK = {
    STRONG: 0,
    DEEP: 1,
    MODERATE: 2,
    WATCHLIST: 3,
    AVOID: 4,
}

SIGNAL_ALIASES = {
    "strong": {STRONG},
    "moderate": {MODERATE},
    "deep": {DEEP},
    "watchlist": {WATCHLIST},
    "avoid": {AVOID},
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
    quality_score: float | None = None,
    min_quality: float = 0,
    bankruptcy_risk: str = "unknown",
) -> str:
    """Choose one label.

    Strong Value needs the screen, a margin of safety at the strong
    threshold, a quality score at the configured minimum, and agreement
    among the going-concern methods. A quality miss or a thin discount
    stays on the Watchlist. A net-net with no earnings support is Deep
    Value even when the ordinary earnings screen fails, provided the
    liquidity floors still hold. Distress that is not a net-net is Avoid.
    """
    quality_ok = min_quality <= 0 or (quality_score is not None and quality_score >= min_quality)
    disagreement = (
        median_mos is not None
        and median_mos >= strong_threshold
        and min_method_mos is not None
        and min_method_mos <= 0
    )
    if net_net and liquidity_ok and not earnings_support:
        return DEEP
    if bankruptcy_risk == "distress" and not net_net:
        return AVOID
    if net_net and liquidity_ok and passed_screen and quality_ok and not disagreement:
        if median_mos is not None and median_mos >= strong_threshold:
            return STRONG
    if passed_screen and median_mos is not None and median_mos >= strong_threshold and quality_ok and not disagreement:
        return STRONG
    if passed_screen and median_mos is not None and median_mos >= strong_threshold and quality_ok and disagreement:
        return MODERATE
    if passed_screen and median_mos is not None and median_mos >= moderate_threshold and quality_ok:
        return MODERATE
    if passed_screen and median_mos is not None and median_mos >= moderate_threshold and not quality_ok:
        return WATCHLIST
    if net_net and liquidity_ok:
        return DEEP
    if passed_screen and (median_mos is None or median_mos >= 0):
        return WATCHLIST
    return AVOID


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
    graham, graham_note = graham_number(
        stock.trailing_eps,
        stock.book_value_per_share,
        stock.shares_outstanding,
    )
    formula, formula_note = graham_formula_value(
        stock.trailing_eps,
        valuation.graham_growth_percent,
        aaa_yield,
        max_growth_percent=valuation.max_graham_growth_percent,
        max_implied_pe=valuation.max_graham_implied_pe,
        shares_outstanding=stock.shares_outstanding,
    )
    dcf_range = conservative_dcf_range(
        stock.free_cash_flow,
        stock.shares_outstanding,
        fcf_history=stock.fcf_history,
        normalize_years=valuation.dcf.normalize_years,
        growth_rate=valuation.dcf.growth_rate,
        years=valuation.dcf.years,
        discount_rate=valuation.dcf.discount_rate,
        terminal_growth=valuation.dcf.terminal_growth,
        max_terminal_share=valuation.dcf.max_terminal_share,
        growth_spread=valuation.dcf.growth_spread,
        discount_spread=valuation.dcf.discount_spread,
    )
    dcf_value = dcf_range.base
    dcf_note = dcf_range.note
    if screen.is_financial:
        dcf_note = f"{dcf_note}; free-cash-flow DCF is a weak fit for financials".strip("; ")

    ncav_strict = None if screen.is_financial else screen.metrics.ncav_strict_per_share
    if screen.is_financial:
        ncav = None
        ncav_note = "not used for financials"
    else:
        ncav, ncav_note = ncav_per_share(
            stock.current_assets,
            stock.total_liabilities,
            stock.shares_outstanding,
        )
        if ncav_strict is not None:
            ncav_note = f"{ncav_note}; strict NCAV {ncav_strict:.2f} per share"
    estimates = [
        MethodEstimate(
            method="Graham Number",
            intrinsic_value_per_share=graham,
            margin_of_safety=margin_of_safety(graham, price),
            note=graham_note,
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
            note=dcf_note,
        ),
        MethodEstimate(
            method="NCAV",
            intrinsic_value_per_share=ncav,
            margin_of_safety=margin_of_safety(ncav, price),
            note=ncav_note,
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
    quality = assess_quality(
        stock,
        settings.scoring.quality,
        is_financial=screen.is_financial,
    )
    signal = classify_signal(
        passed_screen=screen.passed,
        liquidity_ok=screen.liquidity_ok,
        net_net=screen.net_net,
        earnings_support=earnings_support,
        median_mos=median,
        min_method_mos=minimum,
        strong_threshold=valuation.mos.strong,
        moderate_threshold=valuation.mos.moderate,
        quality_score=quality.quality_score,
        min_quality=settings.scoring.quality.minimum,
        bankruptcy_risk=quality.bankruptcy_risk,
    )
    score = composite_score(stock, screen.metrics, median, settings.scoring.weights, quality)
    news = assess_news(stock, settings.news)
    if news.flagged and settings.news.downrank:
        score = round(max(0.0, score - settings.news.penalty), 2)
    reasons = _reasons(
        stock,
        screen,
        estimates,
        median,
        signal,
        aaa_yield,
        quality,
        min_quality=settings.scoring.quality.minimum,
    )
    profile = classify_producer(stock, settings.commodity)
    commodity = profile is not None
    nav, nav_note = (None, "")
    sensitivity: list = []
    if commodity:
        nav, nav_note = commodity_nav_per_share(stock, settings.commodity, settings.valuation.dcf)
        sensitivity = commodity_sensitivity(stock, settings.commodity, settings.valuation.dcf)
    low_cost = is_low_cost_producer(
        is_producer=commodity,
        quality_score=quality.quality_score,
        low_returns=quality.low_returns,
        bankruptcy_risk=quality.bankruptcy_risk,
        free_cash_flow=stock.free_cash_flow,
        debt_to_equity=screen.metrics.debt_to_equity,
        settings=settings.commodity,
    )
    if low_cost:
        reasons.append(
            "Low-cost producer: quality, positive free cash flow, and modest debt. "
            "This is not an all-in sustaining cost."
        )
    elif commodity and nav_note:
        reasons.append(nav_note)
    if news.flagged:
        reasons.append(news.note)
        if settings.news.downrank:
            reasons.append(
                f"Composite score reduced by {settings.news.penalty:g} points because of those headlines"
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
        dcf_low=dcf_range.low,
        dcf_high=dcf_range.high,
        ncav_per_share=ncav,
        ncav_strict_per_share=ncav_strict,
        margin_of_safety=median,
        mos_by_method={item.method: item.margin_of_safety for item in estimates},
        composite_score=score,
        quality_score=quality.quality_score,
        f_score=quality.f_score,
        f_score_available=quality.f_score_available,
        altman_z=quality.altman_z,
        bankruptcy_risk=quality.bankruptcy_risk,
        roic=quality.roic,
        roe=quality.roe,
        low_returns=quality.low_returns,
        negative_news=news.flagged,
        news_headlines=news.headlines,
        reasons=reasons,
        passed_screen=screen.passed,
        screen_failures=screen.failures,
        is_commodity_producer=commodity,
        commodity_name=None if profile is None else profile.commodity,
        commodity_unit=None if profile is None else profile.unit,
        commodity_nav=nav,
        low_cost_producer=low_cost,
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
    quality: QualityAssessment,
    *,
    min_quality: float,
) -> list[str]:
    metrics = screen.metrics
    reasons: list[str] = []
    headline = _headline(estimates, stock, metrics, quality)
    if headline:
        reasons.append(headline)
    if median is not None:
        usable = sum(item.margin_of_safety is not None for item in estimates)
        reasons.append(f"Median margin of safety is {_fmt_pct(median)} across {usable} methods")
    if signal == WATCHLIST and quality.quality_score < min_quality:
        reasons.append(
            f"Quality score {quality.quality_score:.0f} is below the {min_quality:.0f} minimum"
        )
    elif signal == WATCHLIST:
        reasons.append("Multiples pass the screen, but the margin of safety is still thin")
    if signal == MODERATE and any(
        item.margin_of_safety is not None and item.margin_of_safety <= 0 for item in estimates
    ):
        reasons.append("Methods disagree: at least one intrinsic value is below the price")
    if screen.net_net and metrics.ncav_per_share is not None and stock.price is not None:
        reasons.append(
            f"Price {_fmt_price(stock.price)} is below NCAV {_fmt_price(metrics.ncav_per_share)}"
        )
    if metrics.pe_percentile is not None and metrics.peer_group:
        reasons.append(
            f"P/E is cheaper than {1 - metrics.pe_percentile:.0%} of its {metrics.peer_group}"
        )
    if screen.bypassed:
        reasons.append("Earnings and leverage filters were bypassed because price is below NCAV")
    if screen.is_financial:
        reasons.append("Financial company: NCAV and EV/EBIT are not used as primary evidence")
    if screen.failures and signal == AVOID:
        reasons.append("Avoid: " + "; ".join(screen.failures[:4]))
    reasons.extend(quality.notes)
    reasons.append(f"AAA corporate yield used in the Graham formula: {aaa_yield:.2f}%")
    return reasons


def _headline(
    estimates: list[MethodEstimate],
    stock: StockFundamentals,
    metrics,
    quality: QualityAssessment,
) -> str:
    """Compact explanation such as 'Graham Number 35% discount + positive FCF + low debt'."""
    bits: list[str] = []
    discounts = [
        item
        for item in estimates
        if item.margin_of_safety is not None and item.margin_of_safety > 0 and item.method != "NCAV"
    ]
    if discounts:
        best = max(discounts, key=lambda item: item.margin_of_safety or 0)
        bits.append(f"{best.method} {best.margin_of_safety:.0%} discount")
    elif any(item.margin_of_safety is not None and item.margin_of_safety > 0 for item in estimates):
        best = max(
            (item for item in estimates if item.margin_of_safety is not None and item.margin_of_safety > 0),
            key=lambda item: item.margin_of_safety or 0,
        )
        bits.append(f"{best.method} {best.margin_of_safety:.0%} discount")
    if stock.free_cash_flow is not None and stock.free_cash_flow > 0:
        bits.append("positive FCF")
    if metrics.debt_to_equity is not None and metrics.debt_to_equity < 0.5:
        bits.append("low debt")
    if quality.quality_score >= 0:
        bits.append(f"quality {quality.quality_score:.0f}")
    return " + ".join(bits)


def rank_records(
    records: list[SignalRecord],
    min_mos: float,
    *,
    min_quality: float | None = None,
    signal: str | None = None,
) -> list[SignalRecord]:
    """Rank names that clear the margin, quality, and signal filters.

    The default set is Strong Value, Moderate Value, and Deep Value / Net-Net.
    Strong and Moderate names must also clear ``min_mos``. When ``min_quality``
    is set, a stored quality score below it is left out. A snapshot from
    before quality scores were stored has no score, so that floor does not
    hide it. ``signal`` is one of strong, moderate, deep, watchlist, avoid,
    or all.
    """
    if signal == "all":
        allowed = None
    elif signal is None:
        allowed = {STRONG, MODERATE, DEEP}
    else:
        allowed = SIGNAL_ALIASES.get(signal, {STRONG, MODERATE, DEEP})

    def include(record: SignalRecord) -> bool:
        if allowed is not None and record.signal not in allowed:
            return False
        if record.signal in {WATCHLIST, AVOID}:
            return True
        if record.signal in {STRONG, MODERATE}:
            if record.margin_of_safety is None or record.margin_of_safety < min_mos:
                return False
            # A missing quality score is an older snapshot. New scans always store one.
            if (
                min_quality is not None
                and record.quality_score is not None
                and record.quality_score < min_quality
            ):
                return False
        return True

    ranked = [record for record in records if include(record)]
    ranked.sort(
        key=lambda record: (
            -record.composite_score,
            -(record.quality_score if record.quality_score is not None else -1),
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
                "quality_score": record.quality_score,
                "f_score": record.f_score,
                "f_score_available": record.f_score_available,
                "altman_z": record.altman_z,
                "bankruptcy_risk": record.bankruptcy_risk,
                "roic": record.roic,
                "roe": record.roe,
                "low_returns": record.low_returns,
                "negative_news": record.negative_news,
                "news_headlines": " | ".join(record.news_headlines),
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
                "commodity_name": record.commodity_name,
                "commodity_nav": record.commodity_nav,
                "low_cost_producer": record.low_cost_producer,
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
