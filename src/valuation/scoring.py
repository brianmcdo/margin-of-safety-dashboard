"""Composite 0–100 score from margin of safety, cheapness, and quality."""

from __future__ import annotations

from src.config import ScoringWeights
from src.data.models import StockFundamentals, as_float
from src.screening.filters import ComputedMetrics
from src.valuation.quality import QualityAssessment, assess_quality


def margin_of_safety(intrinsic_value: float | None, price: float | None) -> float | None:
    """(intrinsic value - price) / intrinsic value.

    The denominator is intrinsic value, so a stock at half of value has a
    50% margin of safety. Non-positive intrinsic values abstain: a negative
    asset value is not a discount.
    """
    value = as_float(intrinsic_value)
    market = as_float(price)
    if value is None or market is None or value <= 0 or market <= 0:
        return None
    return (value - market) / value


def median_margin(values: list[float | None]) -> float | None:
    usable = sorted(value for value in values if value is not None)
    if not usable:
        return None
    mid = len(usable) // 2
    if len(usable) % 2 == 1:
        return usable[mid]
    return (usable[mid - 1] + usable[mid]) / 2


def _unit_interval(value: float) -> float:
    return max(0.0, min(1.0, value))


def _mos_unit(median_mos: float | None) -> float:
    """Map -20% margin to 0 and +60% margin to 1."""
    if median_mos is None:
        return 0.0
    return _unit_interval((median_mos - (-0.20)) / 0.80)


def _cheap_absolute(pe: float | None, pb: float | None, ev_ebit: float | None) -> float | None:
    parts: list[float] = []
    if pe is not None:
        if pe < 8:
            parts.append(1.0)
        elif pe < 12:
            parts.append(0.7)
        elif pe < 15:
            parts.append(0.45)
        elif pe < 20:
            parts.append(0.2)
        else:
            parts.append(0.0)
    if pb is not None:
        if pb < 0.8:
            parts.append(1.0)
        elif pb < 1.2:
            parts.append(0.75)
        elif pb < 1.5:
            parts.append(0.5)
        elif pb < 2.5:
            parts.append(0.2)
        else:
            parts.append(0.0)
    if ev_ebit is not None:
        if ev_ebit < 6:
            parts.append(1.0)
        elif ev_ebit < 8:
            parts.append(0.75)
        elif ev_ebit < 10:
            parts.append(0.5)
        elif ev_ebit < 14:
            parts.append(0.2)
        else:
            parts.append(0.0)
    if not parts:
        return None
    return sum(parts) / len(parts)


def _cheap_percentile(metrics: ComputedMetrics) -> float | None:
    ranks = [
        metrics.pe_percentile,
        metrics.pb_percentile,
        metrics.ev_ebit_percentile,
    ]
    usable = [1 - rank for rank in ranks if rank is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def _balance_unit(metrics: ComputedMetrics) -> float:
    debt_score = 0.0
    if metrics.debt_to_equity is not None:
        ratio = metrics.debt_to_equity
        if ratio < 0.3:
            debt_score = 1.0
        elif ratio < 0.7:
            debt_score = 0.7
        elif ratio < 1.0:
            debt_score = 0.45
        elif ratio < 1.5:
            debt_score = 0.2
    current_score = 0.0
    if metrics.current_ratio is not None:
        ratio = metrics.current_ratio
        if ratio >= 2:
            current_score = 1.0
        elif ratio >= 1.5:
            current_score = 0.7
        elif ratio >= 1.2:
            current_score = 0.4
        elif ratio >= 1.0:
            current_score = 0.2
    if metrics.debt_to_equity is None and metrics.current_ratio is None:
        return 0.0
    return 0.6 * debt_score + 0.4 * current_score


def composite_score(
    stock: StockFundamentals,
    metrics: ComputedMetrics,
    median_mos: float | None,
    weights: ScoringWeights,
    quality: QualityAssessment | None = None,
) -> float:
    """Weighted score from 0 to 100. Weights are renormalized to sum to 1.

    The three default inputs are margin of safety, relative cheapness, and
    quality. Relative cheapness is the percentile of P/E, P/B, and EV/EBIT
    inside the stock's sector. A sector with fewer than five names uses the
    industry instead. 0 is the cheapest name in that group. A group that is
    still too small falls back to the absolute P/E, P/B, and EV/EBIT buckets.
    A zero weight, including the optional balance-sheet weight, drops out of
    the average.
    """
    absolute = _cheap_absolute(metrics.pe, metrics.pb, metrics.ev_ebit)
    relative = _cheap_percentile(metrics)
    if relative is not None:
        cheap = relative
    elif absolute is not None:
        cheap = absolute
    else:
        cheap = 0.0

    assessment = quality or assess_quality(stock)
    components = {
        "margin_of_safety": _mos_unit(median_mos),
        "cheapness": cheap,
        "balance_sheet": _balance_unit(metrics),
        "quality": _unit_interval(assessment.quality_score / 100),
    }
    weight_map = {
        "margin_of_safety": weights.margin_of_safety,
        "cheapness": weights.cheapness,
        "balance_sheet": weights.balance_sheet,
        "quality": weights.quality,
    }
    total_weight = sum(max(weight, 0.0) for weight in weight_map.values())
    if total_weight <= 0:
        return 0.0
    raw = sum(max(weight_map[name], 0.0) * components[name] for name in components)
    return round(100 * raw / total_weight, 2)
