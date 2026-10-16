"""Net current asset value."""

from src.valuation.ncav import ncav_per_share, strict_ncav_per_share


def test_classic_ncav_per_share():
    assert ncav_per_share(1000, 600, 100) == 4


def test_ncav_abstains_without_shares():
    assert ncav_per_share(1000, 600, 0) is None
    assert ncav_per_share(1000, None, 100) is None


def test_strict_ncav_discounts_receivables_and_inventory():
    # cash 200 + 0.75*300 + 0.50*400 + other 100 = 725; net of 600 liabilities.
    value, note = strict_ncav_per_share(
        current_assets=1000,
        cash=200,
        receivables=300,
        inventory=400,
        total_liabilities=600,
        shares_outstanding=100,
    )
    assert value == 1.25
    assert "75%" in note
    assert "50%" in note


def test_strict_ncav_does_not_let_overlapping_lines_inflate_assets():
    value, note = strict_ncav_per_share(
        current_assets=100,
        cash=80,
        receivables=50,
        inventory=40,
        total_liabilities=30,
        shares_outstanding=10,
    )
    # leftover is negative, so other current assets contribute nothing.
    assert value == (80 + 50 * 0.75 + 40 * 0.50 - 30) / 10
    assert "floored" in note
