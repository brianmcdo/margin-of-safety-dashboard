"""Net current asset value, in the standard form and a stricter liquidation form.

Both functions return ``(value_per_share, note)``. Insufficient data produces
``(None, reason)`` and does not raise. ``net_current_asset_value`` returns
both figures together.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.models import as_float


@dataclass(frozen=True)
class NcavResult:
    """Standard and strict net current asset value, in currency per share.

    Either figure is None when that version cannot be computed. ``note``
    explains both results.
    """

    standard: float | None
    strict: float | None
    note: str


def ncav_per_share(
    current_assets: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
) -> tuple[float | None, str]:
    """Standard NCAV = (current assets − total liabilities) / shares.

    Total liabilities are subtracted, not current liabilities. The residual
    is what common stockholders would have left from current assets after
    every creditor and preferred claim in the simple Graham construction.
    A negative result is returned when the inputs exist: it says the current
    assets do not cover the liabilities. It is not a buy signal.
    """
    try:
        return _ncav_per_share(current_assets, total_liabilities, shares_outstanding)
    except Exception as exc:
        return None, f"NCAV could not be calculated ({exc.__class__.__name__})"


def _ncav_per_share(
    current_assets: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
) -> tuple[float | None, str]:
    assets = as_float(current_assets)
    liabilities = as_float(total_liabilities)
    shares = as_float(shares_outstanding)
    if assets is None or liabilities is None:
        return None, "NCAV needs current assets and total liabilities"
    if shares is None:
        return None, "NCAV needs shares outstanding"
    if shares <= 0:
        return None, "NCAV needs a positive share count"
    return (assets - liabilities) / shares, "current assets minus total liabilities, per share"


def strict_ncav_per_share(
    current_assets: float | None,
    cash: float | None,
    receivables: float | None,
    inventory: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
    *,
    receivables_factor: float = 0.75,
    inventory_factor: float = 0.50,
) -> tuple[float | None, str]:
    """Strict NCAV = (cash + discounted receivables + discounted inventory + other) / shares, minus liabilities.

    The liquidation form is:

        cash
        + receivables × receivables factor (default 0.75)
        + inventory × inventory factor (default 0.50)
        + other current assets
        − total liabilities

    Cash is taken in full. Receivables and inventory are haircut because
    both can shrink in a liquidation. Other current assets are the remainder
    of current assets after cash, receivables, and inventory, included in
    full. A negative remainder, which usually means the line items overlap,
    is floored at zero so the overlap cannot inflate the asset value.
    """
    try:
        return _strict_ncav_per_share(
            current_assets,
            cash,
            receivables,
            inventory,
            total_liabilities,
            shares_outstanding,
            receivables_factor=receivables_factor,
            inventory_factor=inventory_factor,
        )
    except Exception as exc:
        return None, f"strict NCAV could not be calculated ({exc.__class__.__name__})"


def _strict_ncav_per_share(
    current_assets: float | None,
    cash: float | None,
    receivables: float | None,
    inventory: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
    *,
    receivables_factor: float,
    inventory_factor: float,
) -> tuple[float | None, str]:
    receivable_discount = as_float(receivables_factor)
    inventory_discount = as_float(inventory_factor)
    if (
        receivable_discount is None
        or inventory_discount is None
        or not 0 <= receivable_discount <= 1
        or not 0 <= inventory_discount <= 1
    ):
        return None, "NCAV discounts must be between 0 and 1"
    assets = as_float(current_assets)
    cash_value = as_float(cash)
    receivable_value = as_float(receivables)
    inventory_value = as_float(inventory)
    liabilities = as_float(total_liabilities)
    shares = as_float(shares_outstanding)
    if cash_value is None or receivable_value is None or inventory_value is None or liabilities is None:
        return None, "strict NCAV needs cash, receivables, inventory, and liabilities"
    if shares is None:
        return None, "strict NCAV needs shares outstanding"
    if shares <= 0:
        return None, "strict NCAV needs a positive share count"
    other = 0.0
    note = (
        f"receivables at {receivable_discount:.0%} and inventory at {inventory_discount:.0%}"
    )
    if assets is not None:
        leftover = assets - cash_value - receivable_value - inventory_value
        if leftover > 0:
            other = leftover
        elif leftover < 0:
            note += "; overlapping current-asset lines were floored at zero"
    adjusted = (
        cash_value
        + receivable_value * receivable_discount
        + inventory_value * inventory_discount
        + other
    )
    return (adjusted - liabilities) / shares, note


def net_current_asset_value(
    current_assets: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
    *,
    cash: float | None = None,
    receivables: float | None = None,
    inventory: float | None = None,
    receivables_factor: float = 0.75,
    inventory_factor: float = 0.50,
) -> NcavResult:
    """Return the standard NCAV and the stricter discounted NCAV together.

    The strict figure stays None when cash, receivables, or inventory is
    missing. The standard figure can still be returned from current assets
    and total liabilities.
    """
    standard, standard_note = ncav_per_share(current_assets, total_liabilities, shares_outstanding)
    strict, strict_note = strict_ncav_per_share(
        current_assets,
        cash,
        receivables,
        inventory,
        total_liabilities,
        shares_outstanding,
        receivables_factor=receivables_factor,
        inventory_factor=inventory_factor,
    )
    return NcavResult(standard, strict, f"{standard_note}; {strict_note}")
