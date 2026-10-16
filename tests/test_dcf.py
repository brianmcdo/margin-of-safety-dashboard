"""Conservative discounted cash flow."""

import pytest

from src.valuation.dcf import conservative_dcf_per_share, normalized_free_cash_flow


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


def test_dcf_abstains_on_non_positive_cash_flow_or_an_invalid_terminal_rate():
    assert conservative_dcf_per_share(-5, 10)[0] is None
    assert conservative_dcf_per_share(100, 10, discount_rate=0.02, terminal_growth=0.03)[0] is None


def test_normalized_free_cash_flow_uses_the_median_and_keeps_loss_years():
    value, note = normalized_free_cash_flow(999, [10, -2, 40], years=3)
    assert value == 10
    assert "median" in note
