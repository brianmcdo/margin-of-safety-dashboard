"""Graham Number and the interest-rate-adjusted Graham valuation formula."""

from __future__ import annotations

import math

from src.data.models import as_float


def graham_number(eps: float | None, book_value_per_share: float | None) -> float | None:
    """Defensive price ceiling: sqrt(22.5 * EPS * book value per share).

    22.5 is 15 times 1.5, Graham's old ceilings for the P/E and price-to-book
    of a defensive stock. Both inputs must be positive. A negative book value
    or a loss year makes the square root undefined, so the method abstains
    instead of inventing a value.
    """
    earnings = as_float(eps)
    book = as_float(book_value_per_share)
    if earnings is None or book is None or earnings <= 0 or book <= 0:
        return None
    return math.sqrt(22.5 * earnings * book)


def graham_formula_value(
    eps: float | None,
    growth_percent: float,
    aaa_yield_percent: float,
    *,
    max_growth_percent: float = 15,
    max_implied_pe: float | None = 40,
) -> tuple[float | None, str]:
    """Revised Graham intrinsic value.

    V = EPS * (8.5 + 2g) * 4.4 / Y

    g is the expected growth rate in whole-number percent (6 means 6%).
    Y is the AAA corporate bond yield, also in percent. 8.5 is Graham's
    no-growth P/E. 4.4 was the AAA yield when he scaled the formula, so
    multiplying by 4.4 / Y shrinks the value when bonds yield more than
    they did then, and expands it when they yield less.

    Growth above max_growth_percent is cut back: the linear 2g term was
    never meant for high-growth stories. The implied P/E can also be capped
    so a very low yield cannot manufacture a huge multiple.
    """
    earnings = as_float(eps)
    growth = as_float(growth_percent)
    yield_percent = as_float(aaa_yield_percent)
    if earnings is None or earnings <= 0:
        return None, "Graham formula needs positive EPS"
    if growth is None or yield_percent is None:
        return None, "Graham formula needs a growth rate and an AAA yield"
    # Callers sometimes pass a decimal yield (0.062) instead of 6.2.
    if 0 < yield_percent < 1:
        yield_percent *= 100
    if yield_percent <= 0:
        return None, "AAA yield must be positive"
    note = ""
    if growth > max_growth_percent:
        note = (
            f"growth {growth:.1f}% capped at {max_growth_percent:.1f}% "
            "before applying the formula"
        )
        growth = max_growth_percent
    if growth < 0:
        growth = 0
        note = "negative growth reset to 0% for the Graham formula"
    implied_pe = (8.5 + 2 * growth) * 4.4 / yield_percent
    if max_implied_pe is not None and implied_pe > max_implied_pe:
        cap_note = f"implied P/E {implied_pe:.1f} capped at {max_implied_pe:.1f}"
        note = f"{note}; {cap_note}" if note else cap_note
        implied_pe = max_implied_pe
    return earnings * implied_pe, note
