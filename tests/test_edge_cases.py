"""Valuation and scoring on the offline sample universe, including awkward statements."""

import pytest

from src.config import Settings
from src.pipeline import run_pipeline
from src.screening.filters import compute_metrics, screen_stock
from src.signals.generator import AVOID, build_record
from src.valuation.dcf import conservative_dcf_per_share
from src.valuation.graham import graham_formula_value, graham_number
from src.valuation.ncav import ncav_per_share
from src.valuation.scoring import composite_score
from tests.fixtures import (
    healthy_stock,
    high_debt_stock,
    missing_data_stock,
    negative_earnings_stock,
    sample_universe,
    zero_book_stock,
)


def test_sample_universe_is_valued_without_a_network_call(tmp_path):
    settings = Settings()
    settings.data.sqlite_path = str(tmp_path / "signals.db")
    stocks = sample_universe()
    result = run_pipeline(settings, universe="fixture", fundamentals=stocks, aaa_yield=6)
    assert result.failures == []
    assert {record.ticker for record in result.records} == {stock.ticker for stock in stocks}
    assert all(0 <= record.composite_score <= 100 for record in result.records)


def test_negative_earnings_abstain_from_graham_and_still_produce_a_record():
    stock = negative_earnings_stock()
    value, note = graham_number(stock.trailing_eps, stock.book_value_per_share)
    assert value is None
    assert "positive EPS" in note
    value, note = graham_formula_value(stock.trailing_eps, 5, 6)
    assert value is None
    assert "positive EPS" in note
    dcf, _dcf_note = conservative_dcf_per_share(stock.free_cash_flow, stock.shares_outstanding)
    assert dcf is not None and dcf > 0
    record = _record(stock)
    assert record.graham_number is None
    assert record.dcf_value is not None


def test_missing_statements_abstain_instead_of_inventing_a_value():
    stock = missing_data_stock()
    assert graham_number(stock.trailing_eps, stock.book_value_per_share)[0] is None
    assert graham_formula_value(stock.trailing_eps, 5, 6)[0] is None
    assert ncav_per_share(stock.current_assets, stock.total_liabilities, stock.shares_outstanding)[0] is None
    assert conservative_dcf_per_share(stock.free_cash_flow, stock.shares_outstanding)[0] is None
    record = _record(stock)
    assert record.graham_number is None
    assert record.ncav_per_share is None
    assert record.dcf_value is None
    assert record.signal == AVOID


def test_very_high_debt_fails_the_leverage_screen_and_still_scores():
    stock = high_debt_stock()
    settings = Settings()
    metrics = compute_metrics(stock, settings)
    assert metrics.debt_to_equity == 8
    screen = screen_stock(stock, metrics, settings)
    assert any("debt/equity" in failure for failure in screen.failures)
    record = build_record(stock, screen, settings, aaa_yield=6)
    assert record.signal == AVOID
    assert 0 <= record.composite_score <= 100
    score = composite_score(stock, metrics, record.margin_of_safety, settings.scoring.weights)
    assert score == pytest.approx(record.composite_score)


def test_zero_book_value_abstains_from_the_graham_number_and_price_to_book():
    stock = zero_book_stock()
    value, note = graham_number(stock.trailing_eps, stock.book_value_per_share)
    assert value is None
    assert "book value" in note
    metrics = compute_metrics(stock, Settings())
    assert metrics.pb is None
    record = _record(stock)
    assert record.graham_number is None
    assert record.signal == AVOID


def test_one_exploding_ticker_does_not_abort_the_rest_of_the_universe(tmp_path, monkeypatch):
    settings = Settings()
    settings.data.sqlite_path = str(tmp_path / "signals.db")
    good = healthy_stock()
    bad = healthy_stock(ticker="BAD", name="Bad Co")

    real = build_record

    def explode(stock, screen, settings, aaa_yield):
        if stock.ticker == "BAD":
            raise RuntimeError("statement exploded")
        return real(stock, screen, settings, aaa_yield)

    monkeypatch.setattr("src.pipeline.build_record", explode)
    result = run_pipeline(
        settings,
        universe="fixture",
        fundamentals=[bad, good],
        aaa_yield=6,
    )
    assert [record.ticker for record in result.records] == ["VAL"]
    assert result.failures[0].ticker == "BAD"
    assert "valuation failed" in result.failures[0].error


def _record(stock):
    settings = Settings()
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    return build_record(stock, screen, settings, aaa_yield=6)
