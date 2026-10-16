"""Conservative equity DCF on normalized free cash flow."""

from __future__ import annotations

import statistics

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
    """
    cash_flow = as_float(free_cash_flow)
    shares = as_float(shares_outstanding)
    if cash_flow is None or shares is None or shares <= 0:
        return None, "DCF needs free cash flow and a positive share count"
    if cash_flow <= 0:
        return None, "DCF abstains when normalized free cash flow is not positive"
    if years < 1:
        return None, "DCF needs at least one forecast year"
    if discount_rate <= 0:
        return None, "discount rate must be positive"
    if terminal_growth >= discount_rate:
        return None, "terminal growth must be below the discount rate"
    if not 0 < max_terminal_share < 1:
        return None, "terminal-value cap must be between 0 and 1"

    present_explicit = 0.0
    projected = cash_flow
    for year in range(1, years + 1):
        projected *= 1 + growth_rate
        present_explicit += projected / (1 + discount_rate) ** year

    terminal = projected * (1 + terminal_growth) / (discount_rate - terminal_growth)
    present_terminal = terminal / (1 + discount_rate) ** years
    total = present_explicit + present_terminal
    note = (
        f"{years}-year forecast at {growth_rate:.1%} growth, "
        f"discounted at {discount_rate:.1%}, terminal growth {terminal_growth:.1%}"
    )
    if total <= 0:
        return None, "DCF present value was not positive"
    terminal_share = present_terminal / total
    if terminal_share > max_terminal_share:
        # Solve present_terminal' / (explicit + present_terminal') = cap.
        present_terminal = present_explicit * max_terminal_share / (1 - max_terminal_share)
        total = present_explicit + present_terminal
        note += f"; terminal value capped at {max_terminal_share:.0%} of total value"
    return total / shares, note
