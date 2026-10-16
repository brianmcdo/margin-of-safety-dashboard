"""Graham Number and the revised Graham formula."""

import math

import pytest

from src.valuation.graham import graham_formula_value, graham_number


def test_graham_number_matches_the_closed_form():
    # sqrt(22.5 * 4 * 20) = sqrt(1800)
    value, note = graham_number(4, 20)
    assert value == math.sqrt(1800)
    assert "22.5" in note


def test_graham_number_abstains_on_negative_eps_missing_data_and_zero_shares():
    value, note = graham_number(-1, 20)
    assert value is None
    assert "positive EPS" in note
    value, note = graham_number(4, 0)
    assert value is None
    assert "book value" in note
    value, note = graham_number(None, 20)
    assert value is None
    assert "needs EPS" in note
    value, note = graham_number(4, 20, shares_outstanding=0)
    assert value is None
    assert "share count" in note


def test_revised_graham_formula_matches_the_worked_example():
    # V = 4 * (8.5 + 2*6) * 4.4 / 6.2
    value, note = graham_formula_value(4, 6, 6.2, max_implied_pe=None)
    assert value == 4 * (8.5 + 12) * 4.4 / 6.2
    assert note == ""


def test_revised_formula_rejects_negative_eps_and_zero_shares():
    value, note = graham_formula_value(-2, 5, 6)
    assert value is None
    assert "positive EPS" in note
    value, note = graham_formula_value(4, 5, 6, shares_outstanding=0)
    assert value is None
    assert "share count" in note


def test_revised_formula_accepts_a_decimal_yield_and_caps_the_multiple():
    uncapped, _ = graham_formula_value(5, 5, 2, max_implied_pe=None)
    capped, note = graham_formula_value(5, 5, 0.02, max_implied_pe=40)
    assert uncapped == pytest.approx(5 * (8.5 + 10) * 4.4 / 2)
    assert capped == 5 * 40
    assert "capped" in note


def test_graham_methods_abstain_on_non_numeric_inputs():
    value, note = graham_number("n/a", 20)
    assert value is None
    assert "needs EPS" in note
    value, note = graham_number(float("inf"), 20)
    assert value is None
    assert "needs EPS" in note
    value, note = graham_formula_value(4, "fast", 6)
    assert value is None
    assert "growth" in note
    value, note = graham_formula_value(4, 5, 6, max_implied_pe=0)
    assert value is None
    assert "P/E cap" in note
