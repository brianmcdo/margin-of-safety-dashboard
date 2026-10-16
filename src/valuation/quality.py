"""Quality and distress checks that sit beside the value score.

The Piotroski F-score is the nine-point version: four profitability tests,
three leverage and liquidity tests, and two operating-efficiency tests.
A point is awarded only when the inputs for that test exist. Missing years
are left unscored rather than treated as failures.

Altman Z uses the original manufacturing weights when every term is present.
Financials do not get a Z classification, because the model was not built
for banks and insurers. A separate distress flag still trips on negative
book value.

ROIC is after-tax EBIT divided by equity plus debt minus cash. ROE is net
income divided by equity. Consistently low returns means the median of at
least two annual observations is below the configured threshold.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from src.config import QualitySettings
from src.data.models import StockFundamentals, as_float


@dataclass(frozen=True)
class QualityAssessment:
    """One company's quality snapshot. Scores are None when the inputs are missing."""

    f_score: int
    f_score_available: int
    altman_z: float | None
    bankruptcy_risk: str
    roic: float | None
    roe: float | None
    low_returns: bool
    quality_score: float
    notes: tuple[str, ...]


def assess_quality(
    stock: StockFundamentals,
    settings: QualitySettings | None = None,
    *,
    is_financial: bool = False,
) -> QualityAssessment:
    """Score profitability, leverage, distress, and returns from 0 to 100.

    Unknown inputs stay neutral inside the 0–100 score. A computed distress
    zone or a string of weak returns pulls the score down. The function
    returns an assessment instead of raising when a statement line is missing.
    """
    try:
        return _assess_quality(stock, settings or QualitySettings(), is_financial=is_financial)
    except Exception as exc:
        return QualityAssessment(
            f_score=0,
            f_score_available=0,
            altman_z=None,
            bankruptcy_risk="unknown",
            roic=None,
            roe=None,
            low_returns=False,
            quality_score=50.0,
            notes=(f"Quality score could not be calculated ({exc.__class__.__name__})",),
        )


def _assess_quality(
    stock: StockFundamentals,
    settings: QualitySettings,
    *,
    is_financial: bool,
) -> QualityAssessment:
    signals = _f_signals(stock)
    earned = sum(1 for passed in signals.values() if passed is True)
    available = sum(1 for passed in signals.values() if passed is not None)
    altman_z = None if is_financial else _altman_z(stock)
    risk = _bankruptcy_risk(stock, altman_z, settings, is_financial=is_financial)
    roic = _roic(stock, settings.tax_rate)
    roe = _roe(stock)
    roic_history = _annual_roic(stock, settings.tax_rate)
    roe_history = _annual_roe(stock)
    preferred_history = roe_history if is_financial or not roic_history else roic_history
    low_returns = _consistently_low(preferred_history, settings)
    current_return = roe if is_financial and roe is not None else (roic if roic is not None else roe)
    returns_unit = _return_unit(
        statistics.median(preferred_history) if len(preferred_history) >= 2 else current_return
    )
    if low_returns:
        returns_unit = min(returns_unit, 0.2)

    f_unit = _coverage_blend(earned, available, len(signals))
    distress_unit = {"safe": 1.0, "grey": 0.4, "distress": 0.0}.get(risk, 0.5)
    weights = (
        (settings.f_weight, f_unit),
        (settings.distress_weight, distress_unit),
        (settings.returns_weight, returns_unit),
    )
    total_weight = sum(max(weight, 0.0) for weight, _unit in weights)
    if total_weight <= 0:
        quality_score = 50.0
    else:
        quality_score = round(
            100 * sum(max(weight, 0.0) * unit for weight, unit in weights) / total_weight,
            2,
        )
    return QualityAssessment(
        f_score=earned,
        f_score_available=available,
        altman_z=altman_z,
        bankruptcy_risk=risk,
        roic=roic,
        roe=roe,
        low_returns=low_returns,
        quality_score=quality_score,
        notes=_notes(
            earned,
            available,
            altman_z,
            risk,
            roic,
            roe,
            low_returns,
            settings.low_return_threshold,
            is_financial=is_financial,
        ),
    )


def _f_signals(stock: StockFundamentals) -> dict[str, bool | None]:
    """Nine Piotroski tests. None means that test had no data."""
    assets_now = _at(stock.total_assets_history, 0, stock.total_assets)
    assets_then = _at(stock.total_assets_history, 1, None)
    income_now = _at(stock.net_income_history, 0, _implied_net_income(stock))
    income_then = _at(stock.net_income_history, 1, None)
    cash_now = _at(stock.operating_cash_flow_history, 0, stock.operating_cash_flow)
    roa_now = _ratio(income_now, assets_now)
    roa_then = _ratio(income_then, assets_then)

    cash_positive = cash_now if cash_now is not None else as_float(stock.free_cash_flow)
    leverage_now, leverage_then = _leverage_pair(stock)
    liquidity_now, liquidity_then = _liquidity_pair(stock)
    shares_now = _at(stock.shares_history, 0, stock.shares_outstanding)
    shares_then = _at(stock.shares_history, 1, None)
    margin_now, margin_then = _margin_pair(stock)
    turnover_now = _ratio(_at(stock.revenue_history, 0, stock.revenue), assets_now)
    turnover_then = _ratio(_at(stock.revenue_history, 1, None), assets_then)

    return {
        "positive_roa": None if roa_now is None else roa_now > 0,
        "positive_operating_cash_flow": None if cash_positive is None else cash_positive > 0,
        "improving_roa": None if roa_now is None or roa_then is None else roa_now > roa_then,
        "cash_earnings": None if cash_now is None or income_now is None else cash_now > income_now,
        "falling_leverage": _improved(leverage_then, leverage_now, lower_is_better=True)
        if leverage_then is not None
        else _level_leverage(stock),
        "improving_liquidity": _improved(liquidity_then, liquidity_now, lower_is_better=False)
        if liquidity_then is not None
        else _level_liquidity(stock),
        "no_dilution": None if shares_now is None or shares_then is None else shares_now <= shares_then * 1.01,
        "improving_margin": _improved(margin_then, margin_now, lower_is_better=False)
        if margin_then is not None
        else _level_margin(margin_now),
        "improving_turnover": None
        if turnover_now is None or turnover_then is None
        else turnover_now > turnover_then,
    }


def _altman_z(stock: StockFundamentals) -> float | None:
    """Original Altman Z. All five terms are required.

    Z = 1.2×(working capital / assets) + 1.4×(retained earnings / assets)
        + 3.3×(EBIT / assets) + 0.6×(market equity / liabilities)
        + 1.0×(sales / assets)

    Above 2.99 is the safe zone. Below 1.81 is the distress zone.
    """
    assets = as_float(stock.total_assets)
    liabilities = as_float(stock.total_liabilities)
    current_assets = as_float(stock.current_assets)
    current_liabilities = as_float(stock.current_liabilities)
    retained = as_float(stock.retained_earnings)
    ebit = as_float(stock.ebit)
    revenue = as_float(stock.revenue)
    market = as_float(stock.market_cap)
    if (
        assets is None
        or assets <= 0
        or liabilities is None
        or liabilities <= 0
        or current_assets is None
        or current_liabilities is None
        or retained is None
        or ebit is None
        or revenue is None
        or market is None
        or market <= 0
    ):
        return None
    working_capital = current_assets - current_liabilities
    return (
        1.2 * (working_capital / assets)
        + 1.4 * (retained / assets)
        + 3.3 * (ebit / assets)
        + 0.6 * (market / liabilities)
        + 1.0 * (revenue / assets)
    )


def _bankruptcy_risk(
    stock: StockFundamentals,
    altman_z: float | None,
    settings: QualitySettings,
    *,
    is_financial: bool,
) -> str:
    stored_equity = as_float(stock.total_equity)
    if stored_equity is not None and stored_equity <= 0:
        return "distress"
    book = as_float(stock.book_value_per_share)
    if stored_equity is None and book is not None and book <= 0:
        return "distress"
    if altman_z is not None:
        if altman_z < settings.altman_distress:
            return "distress"
        if altman_z < settings.altman_safe:
            return "grey"
        return "safe"
    if is_financial:
        return "unknown"
    current_ratio = as_float(stock.current_ratio)
    leverage = as_float(stock.debt_to_equity)
    ebit = as_float(stock.ebit)
    eps = as_float(stock.trailing_eps)
    weak_liquidity = current_ratio is not None and current_ratio < 1
    heavy_debt = leverage is not None and leverage > 1.5
    losses = (ebit is not None and ebit <= 0) or (eps is not None and eps < 0)
    if weak_liquidity and heavy_debt and losses:
        return "distress"
    if weak_liquidity and heavy_debt:
        return "grey"
    return "unknown"


def _roic(stock: StockFundamentals, tax_rate: float) -> float | None:
    """After-tax EBIT / (equity + debt − cash). Cash is omitted when unknown."""
    ebit = as_float(stock.ebit)
    equity = _equity(stock)
    tax = as_float(tax_rate)
    if ebit is None or equity is None or equity <= 0 or tax is None or not 0 <= tax < 1:
        return None
    debt = as_float(stock.total_debt) or 0.0
    cash = as_float(stock.cash) or 0.0
    invested = equity + debt - cash
    if invested <= 0:
        return None
    return ebit * (1 - tax) / invested


def _roe(stock: StockFundamentals) -> float | None:
    income = _implied_net_income(stock)
    equity = _equity(stock)
    if income is None or equity is None or equity <= 0:
        return None
    return income / equity


def _annual_roic(stock: StockFundamentals, tax_rate: float) -> list[float]:
    tax = as_float(tax_rate)
    if tax is None or not 0 <= tax < 1:
        return []
    values: list[float] = []
    for index, ebit in enumerate(stock.ebit_history):
        earnings = as_float(ebit)
        equity = _at(stock.equity_history, index, None)
        if earnings is None or equity is None or equity <= 0:
            continue
        debt = _at(stock.total_debt_history, index, None) or 0.0
        cash = _at(stock.cash_history, index, None) or 0.0
        invested = equity + debt - cash
        if invested <= 0:
            continue
        values.append(earnings * (1 - tax) / invested)
    return values


def _annual_roe(stock: StockFundamentals) -> list[float]:
    values: list[float] = []
    for index, income in enumerate(stock.net_income_history):
        earnings = as_float(income)
        equity = _at(stock.equity_history, index, None)
        if earnings is None or equity is None or equity <= 0:
            continue
        values.append(earnings / equity)
    return values


def _consistently_low(history: list[float], settings: QualitySettings) -> bool:
    if len(history) < max(2, settings.min_return_years):
        return False
    threshold = as_float(settings.low_return_threshold)
    if threshold is None:
        return False
    return float(statistics.median(history)) < threshold


def _return_unit(rate: float | None) -> float:
    if rate is None:
        return 0.5
    if rate >= 0.15:
        return 1.0
    if rate >= 0.10:
        return 0.75
    if rate >= 0.08:
        return 0.55
    if rate >= 0.04:
        return 0.25
    return 0.0


def _coverage_blend(earned: int, available: int, total: int) -> float:
    """Shrink a partial F-score toward neutral so missing years are not zeros."""
    if available <= 0 or total <= 0:
        return 0.5
    coverage = available / total
    return (earned / available) * coverage + 0.5 * (1 - coverage)


def _notes(
    earned: int,
    available: int,
    altman_z: float | None,
    risk: str,
    roic: float | None,
    roe: float | None,
    low_returns: bool,
    threshold: float,
    *,
    is_financial: bool,
) -> tuple[str, ...]:
    lines: list[str] = []
    if available:
        lines.append(f"Piotroski F-score {earned}/{available}")
    if is_financial:
        lines.append("Altman Z is not applied to financials")
    elif altman_z is not None:
        lines.append(f"Altman Z {altman_z:.2f} ({risk})")
    elif risk != "unknown":
        lines.append(f"Bankruptcy risk flag: {risk}")
    if roic is not None and not is_financial:
        lines.append(f"ROIC {roic:.1%}")
    elif roe is not None:
        lines.append(f"ROE {roe:.1%}")
    if low_returns:
        lines.append(f"Returns have been below {threshold:.0%} across recent years")
    return tuple(lines)


def _equity(stock: StockFundamentals) -> float | None:
    stored = as_float(stock.total_equity)
    if stored is not None and stored > 0:
        return stored
    book = as_float(stock.book_value_per_share)
    shares = as_float(stock.shares_outstanding)
    if book is None or shares is None or book <= 0 or shares <= 0:
        return None
    return book * shares


def _implied_net_income(stock: StockFundamentals) -> float | None:
    stored = as_float(stock.net_income)
    if stored is not None:
        return stored
    earnings = as_float(stock.trailing_eps)
    shares = as_float(stock.shares_outstanding)
    if earnings is None or shares is None or shares <= 0:
        return None
    return earnings * shares


def _leverage_pair(stock: StockFundamentals) -> tuple[float | None, float | None]:
    assets_now = _at(stock.total_assets_history, 0, stock.total_assets)
    assets_then = _at(stock.total_assets_history, 1, None)
    debt_now = _at(stock.long_term_debt_history, 0, stock.long_term_debt)
    debt_then = _at(stock.long_term_debt_history, 1, None)
    if debt_now is None and debt_then is None:
        debt_now = _at(stock.total_debt_history, 0, stock.total_debt)
        debt_then = _at(stock.total_debt_history, 1, None)
    return _ratio(debt_now, assets_now), _ratio(debt_then, assets_then)


def _liquidity_pair(stock: StockFundamentals) -> tuple[float | None, float | None]:
    latest = _at(stock.current_ratio_history, 0, stock.current_ratio)
    prior = _at(stock.current_ratio_history, 1, None)
    return latest, prior


def _margin_pair(stock: StockFundamentals) -> tuple[float | None, float | None]:
    gross_now = _ratio(_at(stock.gross_profit_history, 0, stock.gross_profit), _at(stock.revenue_history, 0, stock.revenue))
    gross_then = _ratio(_at(stock.gross_profit_history, 1, None), _at(stock.revenue_history, 1, None))
    if gross_now is not None or gross_then is not None:
        if gross_now is None:
            gross_now = _ratio(as_float(stock.gross_profit), as_float(stock.revenue))
        return gross_now, gross_then
    ebit_now = _ratio(_at(stock.ebit_history, 0, stock.ebit), _at(stock.revenue_history, 0, stock.revenue))
    ebit_then = _ratio(_at(stock.ebit_history, 1, None), _at(stock.revenue_history, 1, None))
    if ebit_now is None:
        ebit_now = _ratio(as_float(stock.ebit), as_float(stock.revenue))
    return ebit_now, ebit_then


def _level_leverage(stock: StockFundamentals) -> bool | None:
    leverage = as_float(stock.debt_to_equity)
    if leverage is None:
        return None
    return leverage < 1


def _level_liquidity(stock: StockFundamentals) -> bool | None:
    ratio = as_float(stock.current_ratio)
    if ratio is None:
        return None
    if ratio >= 1.5:
        return True
    if ratio < 1:
        return False
    return None


def _level_margin(margin: float | None) -> bool | None:
    if margin is None:
        return None
    return margin > 0


def _improved(prior: float | None, latest: float | None, *, lower_is_better: bool) -> bool | None:
    if prior is None or latest is None:
        return None
    return latest < prior if lower_is_better else latest > prior


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _at(values: list[float | None], index: int, fallback: float | None) -> float | None:
    if index < len(values):
        number = as_float(values[index])
        if number is not None:
            return number
    if index == 0:
        return as_float(fallback)
    return None
