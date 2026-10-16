"""Conservative discounted cash flow."""

import pytest

from src.valuation.dcf import conservative_dcf_per_share, conservative_dcf_range, normalized_free_cash_flow


def test_zero_growth_dcf_equals_a_level_perpetuity_when_the_cap_is_loose():
    # Two explicit years of 100 plus a zero-growth terminal value are exactly
    # the same as a perpetuity of 100 starting in year one: 100 / 0.10 = 1000.
    value, note = conservative_dcf_per_share(
        100,
        10,
        growth_rate=0,
        years=2,
        discount_rate=0.10,
        terminal_growth=0,
        max_terminal_share=0.99,
    )
    assert value == pytest.approx(100)
    assert "capped" not in note


def test_terminal_value_is_scaled_down_when_it_dominates():
    value, note = conservative_dcf_per_share(
        100,
        10,
        growth_rate=0,
        years=2,
        discount_rate=0.10,
        terminal_growth=0,
        max_terminal_share=0.65,
    )
    explicit = 100 / 1.1 + 100 / 1.21
    capped_terminal = explicit * 0.65 / 0.35
    assert value == pytest.approx((explicit + capped_terminal) / 10)
    assert "capped" in note


def test_dcf_abstains_on_non_positive_cash_flow_missing_inputs_or_zero_shares():
    value, note = conservative_dcf_per_share(-5, 10)
    assert value is None
    assert "not positive" in note
    value, note = conservative_dcf_per_share(100, 0)
    assert value is None
    assert "positive share count" in note
    value, note = conservative_dcf_per_share(None, 10)
    assert value is None
    assert note == "DCF needs free cash flow"
    value, note = conservative_dcf_per_share(100, 10, discount_rate=0.02, terminal_growth=0.03)
    assert value is None
    assert "terminal growth" in note


def test_normalized_free_cash_flow_uses_the_median_and_keeps_loss_years():
    value, note = normalized_free_cash_flow(999, [10, -2, 40], years=3)
    assert value == 10
    assert "median" in note


def test_dcf_range_discounts_the_median_cash_flow_at_three_assumption_sets():
    # One explicit year and zero terminal growth, with the cap left loose, is
    # value = cash flow × (1 + growth) / discount / shares.
    # The median of 10, -2, and 40 is 10, so the trailing 999 is ignored.
    result = conservative_dcf_range(
        999,
        10,
        fcf_history=[10, -2, 40],
        normalize_years=3,
        growth_rate=0.0,
        years=1,
        discount_rate=0.10,
        terminal_growth=0.0,
        max_terminal_share=0.99,
        growth_spread=0.02,
        discount_spread=0.02,
    )
    assert result.low == pytest.approx(10 * 0.98 / 0.12 / 10)
    assert result.base == pytest.approx(10 / 0.10 / 10)
    assert result.high == pytest.approx(10 * 1.02 / 0.08 / 10)
    assert result.low < result.base < result.high
    assert "median" in result.note
    assert "low " in result.note and "high " in result.note


def test_dcf_range_abstains_instead_of_raising():
    missing = conservative_dcf_range(None, 10)
    assert missing.low is None and missing.base is None and missing.high is None
    assert "free cash flow" in missing.note
    zero_shares = conservative_dcf_range(
        100,
        0,
        years=1,
        terminal_growth=0,
        max_terminal_share=0.99,
    )
    assert zero_shares.base is None
    assert "share" in zero_shares.note
    bad_growth = conservative_dcf_range(100, 10, growth_rate="fast", years=1)
    assert bad_growth.low is None and bad_growth.base is None and bad_growth.high is None
    assert "growth" in bad_growth.note
