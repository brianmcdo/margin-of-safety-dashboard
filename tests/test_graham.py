"""Graham Number and the revised Graham formula."""

import math

import pytest

from src.valuation.graham import graham_formula_value, graham_number


def test_graham_number_matches_the_closed_form():
    # sqrt(22.5 * 4 * 20) = sqrt(1800)
    assert graham_number(4, 20) == math.sqrt(1800)


def test_graham_number_abstains_when_an_input_is_not_positive():
    assert graham_number(-1, 20) is None
    assert graham_number(4, 0) is None
    assert graham_number(None, 20) is None


def test_revised_graham_formula_matches_the_worked_example():
    # V = 4 * (8.5 + 2*6) * 4.4 / 6.2
    value, note = graham_formula_value(4, 6, 6.2, max_implied_pe=None)
    assert value == 4 * (8.5 + 12) * 4.4 / 6.2
    assert note == ""


def test_revised_formula_accepts_a_decimal_yield_and_caps_the_multiple():
    uncapped, _ = graham_formula_value(5, 5, 2, max_implied_pe=None)
    capped, note = graham_formula_value(5, 5, 0.02, max_implied_pe=40)
    assert uncapped == pytest.approx(5 * (8.5 + 10) * 4.4 / 2)
    assert capped == 5 * 40
    assert "capped" in note
