"""Net current asset value."""

from src.valuation.ncav import ncav_per_share, net_current_asset_value, strict_ncav_per_share


def test_classic_ncav_per_share():
    value, note = ncav_per_share(1000, 600, 100)
    assert value == 4
    assert "per share" in note


def test_ncav_distinguishes_missing_data_from_zero_shares():
    value, note = ncav_per_share(1000, 600, 0)
    assert value is None
    assert "positive share count" in note
    value, note = ncav_per_share(1000, None, 100)
    assert value is None
    assert "total liabilities" in note
    value, note = ncav_per_share(1000, 600, None)
    assert value is None
    assert "shares outstanding" in note


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


def test_one_call_returns_the_standard_and_strict_figures():
    result = net_current_asset_value(
        current_assets=1000,
        total_liabilities=600,
        shares_outstanding=100,
        cash=200,
        receivables=300,
        inventory=400,
    )
    assert result.standard == 4
    assert result.strict == 1.25
    assert "75%" in result.note


def test_ncav_abstains_on_bad_inputs_without_raising():
    value, note = ncav_per_share("assets", 600, 100)
    assert value is None
    assert "current assets" in note
    value, note = strict_ncav_per_share(
        1000, 200, 300, 400, 600, 100, receivables_factor=1.5
    )
    assert value is None
    assert "between 0 and 1" in note
    result = net_current_asset_value(1000, 600, 0, cash=1, receivables=1, inventory=1)
    assert result.standard is None
    assert result.strict is None
    assert "positive share count" in result.note
