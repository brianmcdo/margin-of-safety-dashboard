"""Net current asset value, including Graham's discounted variant."""

from __future__ import annotations

from src.data.models import as_float


def ncav_per_share(
    current_assets: float | None,
    total_liabilities: float | None,
    shares_outstanding: float | None,
) -> float | None:
    """Classic net-net: (current assets - total liabilities) / shares.

    Total liabilities, not current liabilities, are subtracted. The residual
    is what common stockholders would have left from current assets after
    every creditor and preferred claim in the simple Graham construction.
    A negative result is returned when the inputs exist: it is informative,
    but it is not a buy signal.
    """
    assets = as_float(current_assets)
    liabilities = as_float(total_liabilities)
    shares = as_float(shares_outstanding)
    if assets is None or liabilities is None or shares is None or shares <= 0:
        return None
    return (assets - liabilities) / shares


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
    """NCAV with haircuts on receivables and inventory.

    Cash is taken in full. Receivables are discounted (default 75 cents on
    the dollar) and inventory is discounted harder (default 50 cents),
    because both can shrink in a liquidation. Any leftover current assets
    that are not cash, receivables, or inventory are included in full.
    A negative leftover, which usually means the line items overlap, is
    floored at zero so the overlap cannot inflate the asset value.
    """
    assets = as_float(current_assets)
    cash_value = as_float(cash)
    receivable_value = as_float(receivables)
    inventory_value = as_float(inventory)
    liabilities = as_float(total_liabilities)
    shares = as_float(shares_outstanding)
    if None in (cash_value, receivable_value, inventory_value, liabilities, shares):
        return None, "strict NCAV needs cash, receivables, inventory, liabilities, and shares"
    if shares is None or shares <= 0:
        return None, "strict NCAV needs a positive share count"
    assert cash_value is not None
    assert receivable_value is not None
    assert inventory_value is not None
    assert liabilities is not None
    other = 0.0
    note = (
        f"receivables at {receivables_factor:.0%} and inventory at {inventory_factor:.0%}"
    )
    if assets is not None:
        leftover = assets - cash_value - receivable_value - inventory_value
        if leftover > 0:
            other = leftover
        elif leftover < 0:
            note += "; overlapping current-asset lines were floored at zero"
    adjusted = (
        cash_value
        + receivable_value * receivables_factor
        + inventory_value * inventory_factor
        + other
    )
    return (adjusted - liabilities) / shares, note
