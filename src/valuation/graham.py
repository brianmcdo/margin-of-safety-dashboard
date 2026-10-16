"""Graham Number and the revised Graham valuation formula.

Each function returns ``(value_per_share, note)``. Insufficient data produces
``(None, reason)`` and does not raise.
"""

from __future__ import annotations

import math

from src.data.models import as_float


def graham_number(
    eps: float | None,
    book_value_per_share: float | None,
    shares_outstanding: float | None = None,
) -> tuple[float | None, str]:
    """Graham Number = sqrt(22.5 × EPS × book value per share).

    22.5 is Graham's defensive P/E ceiling of 15 times his price-to-book
    ceiling of 1.5. A price at the Graham Number is the highest price that
    can satisfy both ceilings at once:

        price / EPS <= 15 and price / book <= 1.5
        price <= sqrt(15 × 1.5 × EPS × book)

    Both inputs have to be positive. A loss or a negative book value makes
    the square root undefined, so the method abstains. Share count is
    optional because the inputs are already per share. A supplied share count
    of zero or less abstains; a missing share count does not.
    """
    try:
        return _graham_number(eps, book_value_per_share, shares_outstanding)
    except Exception as exc:
        return None, f"Graham Number could not be calculated ({exc.__class__.__name__})"


def _graham_number(
    eps: float | None,
    book_value_per_share: float | None,
    shares_outstanding: float | None,
) -> tuple[float | None, str]:
    earnings = as_float(eps)
    book = as_float(book_value_per_share)
    shares = as_float(shares_outstanding)
    if shares is not None and shares <= 0:
        return None, "Graham Number needs a positive share count"
    if earnings is None or book is None:
        return None, "Graham Number needs EPS and book value per share"
    if earnings <= 0:
        return None, "Graham Number needs positive EPS"
    if book <= 0:
        return None, "Graham Number needs positive book value per share"
    return math.sqrt(22.5 * earnings * book), "sqrt(22.5 × EPS × book value per share)"


def graham_formula_value(
    eps: float | None,
    growth_percent: float,
    aaa_yield_percent: float,
    *,
    max_growth_percent: float = 15,
    max_implied_pe: float | None = 40,
    shares_outstanding: float | None = None,
) -> tuple[float | None, str]:
    """Revised Graham value, V = EPS × (8.5 + 2g) × 4.4 / Y.

    g is expected earnings growth in whole-number percent (6 means 6%, not
    0.06). Y is the AAA corporate bond yield, also in percent. 8.5 is
    Graham's no-growth price/earnings ratio. 4.4 was the AAA yield when he
    published the formula, so 4.4 / Y shrinks the value when bonds yield
    more than they did then and expands it when they yield less.

    Example: EPS 4, g 6, Y 6.2 gives 4 × (8.5 + 12) × 4.4 / 6.2.

    Growth above ``max_growth_percent`` is cut back because the linear 2g
    term was not meant for high-growth stories. Negative growth is reset to
    zero rather than producing a negative multiple. The implied P/E can also
    be capped so a very low yield cannot manufacture a huge multiple. Pass
    ``max_implied_pe=None`` to leave the multiple uncapped.
    """
    try:
        return _graham_formula_value(
            eps,
            growth_percent,
            aaa_yield_percent,
            max_growth_percent=max_growth_percent,
            max_implied_pe=max_implied_pe,
            shares_outstanding=shares_outstanding,
        )
    except Exception as exc:
        return None, f"Graham formula could not be calculated ({exc.__class__.__name__})"


def _graham_formula_value(
    eps: float | None,
    growth_percent: float,
    aaa_yield_percent: float,
    *,
    max_growth_percent: float | None,
    max_implied_pe: float | None,
    shares_outstanding: float | None,
) -> tuple[float | None, str]:
    earnings = as_float(eps)
    growth = as_float(growth_percent)
    yield_percent = as_float(aaa_yield_percent)
    shares = as_float(shares_outstanding)
    if shares is not None and shares <= 0:
        return None, "Graham formula needs a positive share count"
    if earnings is None:
        return None, "Graham formula needs EPS"
    if earnings <= 0:
        return None, "Graham formula needs positive EPS"
    if growth is None or yield_percent is None:
        return None, "Graham formula needs a growth rate and an AAA yield"
    growth_cap = as_float(max_growth_percent)
    if growth_cap is None:
        return None, "Graham growth cap must be a finite number"
    pe_cap = None
    if max_implied_pe is not None:
        pe_cap = as_float(max_implied_pe)
        if pe_cap is None or pe_cap <= 0:
            return None, "Graham implied P/E cap must be positive"
    # Callers sometimes pass a decimal yield (0.062) instead of 6.2.
    if 0 < yield_percent < 1:
        yield_percent *= 100
    if yield_percent <= 0:
        return None, "AAA yield must be positive"
    note = ""
    if growth > growth_cap:
        note = (
            f"growth {growth:.1f}% capped at {growth_cap:.1f}% "
            "before applying the formula"
        )
        growth = growth_cap
    if growth < 0:
        growth = 0
        note = "negative growth reset to 0% for the Graham formula"
    implied_pe = (8.5 + 2 * growth) * 4.4 / yield_percent
    if pe_cap is not None and implied_pe > pe_cap:
        cap_note = f"implied P/E {implied_pe:.1f} capped at {pe_cap:.1f}"
        note = f"{note}; {cap_note}" if note else cap_note
        implied_pe = pe_cap
    return earnings * implied_pe, note
