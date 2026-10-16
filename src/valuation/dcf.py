"""Conservative equity DCF on normalized free cash flow.

``conservative_dcf_per_share`` values one assumption set and returns
``(value_per_share, note)``. ``conservative_dcf_range`` values the base case
plus a lower and a higher case. Insufficient data produces None and a reason,
and does not raise.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from src.data.models import as_float


def normalized_free_cash_flow(
    trailing: float | None,
    history: list[float] | None,
    years: int = 3,
) -> tuple[float | None, str]:
    """Median of recent annual free cash flow, falling back to the TTM figure.

    The median is used because a single peak or trough year is a poor base
    for a multi-year projection. Loss years stay in the window. Dropping
    them would make a cyclical business look permanently healthier than it is.
    """
    try:
        return _normalized_free_cash_flow(trailing, history, years)
    except Exception as exc:
        return None, f"free cash flow could not be normalized ({exc.__class__.__name__})"


def _normalized_free_cash_flow(
    trailing: float | None,
    history: list[float] | None,
    years: int,
) -> tuple[float | None, str]:
    if isinstance(years, bool) or not isinstance(years, int) or years < 1:
        return None, "free-cash-flow window must be a positive number of years"
    window: list[float] = []
    for value in (history or [])[: max(years, 1)]:
        number = as_float(value)
        if number is not None:
            window.append(number)
    if len(window) >= 2:
        median = float(statistics.median(window))
        return median, f"median of {len(window)} annual free-cash-flow observations"
    trailing_value = as_float(trailing)
    if trailing_value is not None:
        return trailing_value, "trailing free cash flow; fewer than two annual observations"
    if window:
        return window[0], "only one annual free-cash-flow observation"
    return None, "no free cash flow available"


def conservative_dcf_per_share(
    free_cash_flow: float | None,
    shares_outstanding: float | None,
    *,
    growth_rate: float = 0.03,
    years: int = 8,
    discount_rate: float = 0.10,
    terminal_growth: float = 0.025,
    max_terminal_share: float = 0.65,
) -> tuple[float | None, str]:
    """Discount equity free cash flow and return a per-share equity value.

    Cash flows are equity free cash flow, so the result is not an enterprise
    value and debt is not deducted again. The explicit forecast grows the
    base cash flow for ``years`` at ``growth_rate``. The terminal value is a
    Gordon growth perpetuity at ``terminal_growth``.

    When the discounted terminal value would be more than ``max_terminal_share``
    of the total, it is scaled down. That cap keeps a distant perpetuity from
    dominating a valuation that is supposed to be conservative.

    Pass free cash flow that has already been normalized, or use
    ``conservative_dcf_range``, which takes the median of the annual history
    before it discounts.
    """
    try:
        return _conservative_dcf_per_share(
            free_cash_flow,
            shares_outstanding,
            growth_rate=growth_rate,
            years=years,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            max_terminal_share=max_terminal_share,
        )
    except Exception as exc:
        return None, f"DCF could not be calculated ({exc.__class__.__name__})"


def _conservative_dcf_per_share(
    free_cash_flow: float | None,
    shares_outstanding: float | None,
    *,
    growth_rate: float,
    years: int,
    discount_rate: float,
    terminal_growth: float,
    max_terminal_share: float,
) -> tuple[float | None, str]:
    cash_flow = as_float(free_cash_flow)
    shares = as_float(shares_outstanding)
    growth = as_float(growth_rate)
    discount = as_float(discount_rate)
    terminal_rate = as_float(terminal_growth)
    terminal_cap = as_float(max_terminal_share)
    if cash_flow is None:
        return None, "DCF needs free cash flow"
    if shares is None:
        return None, "DCF needs shares outstanding"
    if shares <= 0:
        return None, "DCF needs a positive share count"
    if cash_flow <= 0:
        return None, "DCF abstains when normalized free cash flow is not positive"
    if not isinstance(years, int) or isinstance(years, bool) or years < 1:
        return None, "DCF needs at least one forecast year"
    if growth is None or discount is None or terminal_rate is None or terminal_cap is None:
        return None, "DCF needs finite growth, discount, and terminal assumptions"
    if growth <= -1:
        return None, "DCF growth rate must be greater than -100%"
    if discount <= 0:
        return None, "discount rate must be positive"
    if terminal_rate >= discount:
        return None, "terminal growth must be below the discount rate"
    if not 0 < terminal_cap < 1:
        return None, "terminal-value cap must be between 0 and 1"

    present_explicit = 0.0
    projected = cash_flow
    for year in range(1, years + 1):
        projected *= 1 + growth
        present_explicit += projected / (1 + discount) ** year

    terminal = projected * (1 + terminal_rate) / (discount - terminal_rate)
    present_terminal = terminal / (1 + discount) ** years
    total = present_explicit + present_terminal
    note = (
        f"{years}-year forecast at {growth:.1%} growth, "
        f"discounted at {discount:.1%}, terminal growth {terminal_rate:.1%}"
    )
    if total <= 0:
        return None, "DCF present value was not positive"
    terminal_share = present_terminal / total
    if terminal_share > terminal_cap:
        # Solve present_terminal' / (explicit + present_terminal') = cap.
        present_terminal = present_explicit * terminal_cap / (1 - terminal_cap)
        total = present_explicit + present_terminal
        note += f"; terminal value capped at {terminal_cap:.0%} of total value"
    return total / shares, note


@dataclass(frozen=True)
class DcfRange:
    """Per-share equity value under three assumption sets.

    ``low`` uses slower growth and a higher discount rate. ``base`` uses the
    configured rates. ``high`` uses faster growth and a lower discount rate.
    A scenario that cannot be valued is None. ``note`` gives the normalized
    cash flow and the reason for any missing scenario.
    """

    low: float | None
    base: float | None
    high: float | None
    note: str


def conservative_dcf_range(
    free_cash_flow: float | None,
    shares_outstanding: float | None,
    *,
    fcf_history: list[float] | None = None,
    normalize_years: int = 3,
    growth_rate: float = 0.03,
    years: int = 8,
    discount_rate: float = 0.10,
    terminal_growth: float = 0.025,
    max_terminal_share: float = 0.65,
    growth_spread: float = 0.02,
    discount_spread: float = 0.02,
) -> DcfRange:
    """Value normalized free cash flow at a low, base, and high assumption set.

    The cash flow is the median of up to ``normalize_years`` annual figures,
    with the trailing figure used only when fewer than two annual observations
    exist. The base case uses ``growth_rate`` and ``discount_rate``. The low
    case subtracts ``growth_spread`` and adds ``discount_spread``. The high
    case adds ``growth_spread`` and subtracts ``discount_spread``. Terminal
    growth and the terminal-value cap stay the same in every case.
    """
    try:
        return _conservative_dcf_range(
            free_cash_flow,
            shares_outstanding,
            fcf_history=fcf_history,
            normalize_years=normalize_years,
            growth_rate=growth_rate,
            years=years,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            max_terminal_share=max_terminal_share,
            growth_spread=growth_spread,
            discount_spread=discount_spread,
        )
    except Exception as exc:
        return DcfRange(
            None,
            None,
            None,
            f"DCF range could not be calculated ({exc.__class__.__name__})",
        )


def _conservative_dcf_range(
    free_cash_flow: float | None,
    shares_outstanding: float | None,
    *,
    fcf_history: list[float] | None,
    normalize_years: int,
    growth_rate: float,
    years: int,
    discount_rate: float,
    terminal_growth: float,
    max_terminal_share: float,
    growth_spread: float,
    discount_spread: float,
) -> DcfRange:
    cash_flow, cash_note = normalized_free_cash_flow(free_cash_flow, fcf_history, normalize_years)
    growth = as_float(growth_rate)
    discount = as_float(discount_rate)
    slower_growth = as_float(growth_spread)
    wider_discount = as_float(discount_spread)
    if growth is None or discount is None:
        return DcfRange(None, None, None, f"{cash_note}; DCF needs finite growth and discount rates")
    if slower_growth is None or wider_discount is None or slower_growth < 0 or wider_discount < 0:
        return DcfRange(None, None, None, f"{cash_note}; DCF spreads must be zero or positive")

    scenarios = (
        ("low", growth - slower_growth, discount + wider_discount),
        ("base", growth, discount),
        ("high", growth + slower_growth, discount - wider_discount),
    )
    values: dict[str, float | None] = {}
    details: dict[str, str] = {}
    for label, scenario_growth, scenario_discount in scenarios:
        value, detail = conservative_dcf_per_share(
            cash_flow,
            shares_outstanding,
            growth_rate=scenario_growth,
            years=years,
            discount_rate=scenario_discount,
            terminal_growth=terminal_growth,
            max_terminal_share=max_terminal_share,
        )
        values[label] = value
        details[label] = detail

    phrases = [
        _scenario_phrase(label, values[label], details[label], scenario_growth, scenario_discount)
        for label, scenario_growth, scenario_discount in scenarios
    ]
    if values["low"] is None and values["base"] is None and values["high"] is None:
        return DcfRange(None, None, None, f"{cash_note}; {details['base']}")
    note = f"{cash_note}; {'; '.join(phrases)}"
    if "capped" in details["base"]:
        note = f"{note}; {details['base'].split(';')[-1].strip()}"
    return DcfRange(values["low"], values["base"], values["high"], note)


def _scenario_phrase(
    label: str,
    value: float | None,
    detail: str,
    growth: float,
    discount: float,
) -> str:
    if value is None:
        return f"{label} unavailable ({detail})"
    return (
        f"{label} {value:.2f} per share at {growth:.1%} growth and {discount:.1%} discount"
    )
