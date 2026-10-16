"""Turn a ranked signal list into a simple diversified basket.

Names are taken in the order given, which should already be the ranking.
Each holding gets the same slot: the smaller of the position cap, the sector
cap, and an equal share of the minimum number of holdings. A name is skipped
when its sector cannot take a full slot. Once the minimum count is met, later
names fill whatever room is left. Weight that cannot be placed stays unallocated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data.models import SignalRecord

_EPS = 1e-9


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str | None
    sector: str
    signal: str
    composite_score: float
    weight: float


@dataclass(frozen=True)
class Portfolio:
    holdings: list[Holding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    max_sector_weight: float = 0.25
    max_position_size: float = 0.10
    min_holdings: int = 8

    @property
    def invested(self) -> float:
        return sum(holding.weight for holding in self.holdings)

    def sector_weights(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for holding in self.holdings:
            totals[holding.sector] = totals.get(holding.sector, 0.0) + holding.weight
        return totals


def build_portfolio(
    ranked: list[SignalRecord],
    *,
    max_sector_weight: float,
    max_position_size: float,
    min_holdings: int,
) -> Portfolio:
    """Suggest weights for the ranked names. The list is not re-sorted."""
    if not 0 < max_sector_weight <= 1:
        raise ValueError("max_sector_weight must be greater than 0 and at most 1")
    if not 0 < max_position_size <= 1:
        raise ValueError("max_position_size must be greater than 0 and at most 1")
    if min_holdings < 1:
        raise ValueError("min_holdings must be at least 1")

    slot = min(max_position_size, max_sector_weight, 1.0 / min_holdings)
    holdings: list[Holding] = []
    sector_weight: dict[str, float] = {}
    seen: set[str] = set()
    remaining = 1.0

    for record in ranked:
        if remaining <= _EPS:
            break
        if record.ticker in seen:
            continue
        sector = (record.sector or "").strip() or "Unknown"
        sector_room = max_sector_weight - sector_weight.get(sector, 0.0)
        weight = remaining if remaining < slot - _EPS else slot
        if weight > sector_room + _EPS or weight > max_position_size + _EPS:
            continue
        weight = round(min(weight, sector_room, max_position_size, remaining), 6)
        if weight <= _EPS:
            continue
        holdings.append(
            Holding(
                ticker=record.ticker,
                name=record.name,
                sector=sector,
                signal=record.signal,
                composite_score=record.composite_score,
                weight=weight,
            )
        )
        seen.add(record.ticker)
        sector_weight[sector] = sector_weight.get(sector, 0.0) + weight
        remaining = round(remaining - weight, 6)

    notes: list[str] = []
    if len(holdings) < min_holdings:
        notes.append(f"{len(holdings)} holdings fit the caps. The minimum is {min_holdings}.")
    if remaining > 0.005:
        skipped = len({record.ticker for record in ranked}) > len(holdings)
        reason = (
            "Another holding would break a position or sector cap."
            if skipped
            else "The ranked list has no further names."
        )
        notes.append(f"{remaining:.0%} of the basket is unallocated. {reason}")
    return Portfolio(
        holdings=holdings,
        notes=notes,
        max_sector_weight=max_sector_weight,
        max_position_size=max_position_size,
        min_holdings=min_holdings,
    )
