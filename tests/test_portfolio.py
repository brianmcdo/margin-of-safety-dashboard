"""Rank-order baskets. No market data."""

import pytest

from src.data.models import SignalRecord
from src.main import build_parser
from src.portfolio.builder import build_portfolio


def _name(ticker: str, sector: str, score: float) -> SignalRecord:
    return SignalRecord(
        ticker=ticker,
        name=ticker,
        sector=sector,
        signal="Strong Value",
        composite_score=score,
    )


def test_a_full_book_respects_position_and_sector_caps():
    ranked = []
    score = 100.0
    for sector in ("Energy", "Financials", "Healthcare", "Industrials", "Technology"):
        for index in range(3):
            ranked.append(_name(f"{sector[:1]}{index}", sector, score))
            score -= 1
    basket = build_portfolio(
        ranked,
        max_sector_weight=0.25,
        max_position_size=0.10,
        min_holdings=8,
    )
    assert len(basket.holdings) == 10
    assert basket.invested == pytest.approx(1.0)
    assert basket.notes == []
    assert all(holding.weight <= 0.10 + 1e-9 for holding in basket.holdings)
    for weight in basket.sector_weights().values():
        assert weight <= 0.25 + 1e-9
    assert [holding.ticker for holding in basket.holdings if holding.sector == "Energy"] == ["E0", "E1"]
    assert basket.holdings[0].ticker == "E0"


def test_a_full_sector_is_skipped_for_the_next_name():
    ranked = [
        _name("E0", "Energy", 90),
        _name("E1", "Energy", 80),
        _name("E2", "Energy", 70),
        _name("H0", "Healthcare", 60),
    ]
    basket = build_portfolio(
        ranked,
        max_sector_weight=0.20,
        max_position_size=0.10,
        min_holdings=2,
    )
    tickers = [holding.ticker for holding in basket.holdings]
    assert tickers == ["E0", "E1", "H0"]
    assert "E2" not in tickers
    assert basket.sector_weights()["Energy"] == pytest.approx(0.20)
    assert any("sector cap" in note for note in basket.notes)


def test_too_few_names_leave_weight_unallocated():
    basket = build_portfolio(
        [_name("AAA", "Energy", 90), _name("BBB", "Healthcare", 80)],
        max_sector_weight=0.25,
        max_position_size=0.10,
        min_holdings=8,
    )
    assert len(basket.holdings) == 2
    assert basket.invested == pytest.approx(0.20)
    assert any("minimum is 8" in note for note in basket.notes)
    assert any("no further names" in note for note in basket.notes)


def test_the_minimum_count_sets_the_slot_when_it_is_tighter_than_the_position_cap():
    ranked = [_name(f"N{index}", f"Sector {index}", 50 - index) for index in range(8)]
    basket = build_portfolio(
        ranked,
        max_sector_weight=0.25,
        max_position_size=0.20,
        min_holdings=8,
    )
    assert len(basket.holdings) == 8
    assert basket.invested == pytest.approx(1.0)
    assert all(holding.weight == pytest.approx(0.125) for holding in basket.holdings)


def test_a_blank_sector_shares_one_unknown_bucket():
    ranked = [_name(f"U{index}", "", 40 - index) for index in range(3)]
    basket = build_portfolio(
        ranked,
        max_sector_weight=0.10,
        max_position_size=0.10,
        min_holdings=1,
    )
    assert [holding.ticker for holding in basket.holdings] == ["U0"]
    assert basket.holdings[0].sector == "Unknown"


def test_duplicate_tickers_are_taken_once():
    ranked = [_name("AAA", "Energy", 90), _name("AAA", "Energy", 90), _name("BBB", "Healthcare", 80)]
    basket = build_portfolio(
        ranked,
        max_sector_weight=1.0,
        max_position_size=0.50,
        min_holdings=2,
    )
    assert [holding.ticker for holding in basket.holdings] == ["AAA", "BBB"]


def test_invalid_caps_are_rejected():
    with pytest.raises(ValueError):
        build_portfolio([], max_sector_weight=0, max_position_size=0.10, min_holdings=8)


def test_cli_portfolio_flags_default_to_config():
    args = build_parser().parse_args([])
    assert args.max_sector_weight is None
    assert args.max_position is None
    assert args.min_holdings is None
    args = build_parser().parse_args(
        ["--max-sector-weight", "0.30", "--max-position", "0.05", "--min-holdings", "12"]
    )
    assert args.max_sector_weight == 0.30
    assert args.max_position == 0.05
    assert args.min_holdings == 12
