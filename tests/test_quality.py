"""Piotroski F-score, Altman Z, and the quality ranking."""

import pytest

from src.config import Settings
from src.data.models import StockFundamentals
from src.screening.filters import compute_metrics, screen_stock
from src.signals.generator import build_record, rank_records
from src.valuation.quality import assess_quality
from tests.test_filters_signals import _stock


def _healthy() -> StockFundamentals:
    return StockFundamentals(
        ticker="GOOD",
        price=40,
        market_cap=800,
        shares_outstanding=100,
        shares_history=[100, 100],
        book_value_per_share=5,
        total_equity=500,
        equity_history=[500, 480],
        total_assets=1000,
        total_assets_history=[1000, 900],
        net_income=50,
        net_income_history=[50, 40],
        operating_cash_flow=80,
        operating_cash_flow_history=[80, 70],
        revenue=1000,
        revenue_history=[1000, 800],
        ebit=150,
        ebit_history=[150, 120],
        gross_profit=400,
        gross_profit_history=[400, 300],
        current_assets=400,
        current_liabilities=200,
        current_ratio=2.0,
        current_ratio_history=[2.0, 1.5],
        total_liabilities=500,
        retained_earnings=300,
        long_term_debt=100,
        long_term_debt_history=[100, 150],
        total_debt=100,
        total_debt_history=[100, 150],
        cash=50,
        cash_history=[50, 40],
        free_cash_flow=60,
    )


def test_piotroski_altman_and_roic_match_a_worked_example():
    # Z = 1.2*(200/1000) + 1.4*(300/1000) + 3.3*(150/1000) + 0.6*(800/500) + 1000/1000
    assessment = assess_quality(_healthy())
    assert assessment.f_score == 9
    assert assessment.f_score_available == 9
    assert assessment.altman_z == pytest.approx(3.115)
    assert assessment.bankruptcy_risk == "safe"
    assert assessment.roic == pytest.approx(150 * 0.79 / 550)
    assert assessment.roe == pytest.approx(0.10)
    assert assessment.low_returns is False
    assert assessment.quality_score == 100


def test_altman_distress_zone_and_a_string_of_weak_returns():
    distressed = _healthy().model_copy(
        update={
            "current_assets": 50,
            "current_liabilities": 400,
            "retained_earnings": 0,
            "ebit": -10,
            "ebit_history": [-10, -12],
            "market_cap": 100,
            "total_liabilities": 900,
            "revenue": 200,
            "revenue_history": [200, 180],
            "net_income": 10,
            "net_income_history": [10, 12],
            "equity_history": [500, 500],
            "operating_cash_flow": 5,
            "operating_cash_flow_history": [5, 4],
        }
    )
    assessment = assess_quality(distressed)
    expected_z = 1.2 * ((50 - 400) / 1000) + 1.4 * 0 + 3.3 * (-10 / 1000) + 0.6 * (100 / 900) + 200 / 1000
    assert assessment.altman_z == pytest.approx(expected_z)
    assert assessment.altman_z < 1.81
    assert assessment.bankruptcy_risk == "distress"

    weak = _healthy().model_copy(
        update={
            "ebit": None,
            "ebit_history": [],
            "net_income": 10,
            "net_income_history": [10, 12],
            "equity_history": [500, 500],
            "total_equity": 500,
        }
    )
    weak_assessment = assess_quality(weak)
    assert weak_assessment.roe == pytest.approx(0.02)
    assert weak_assessment.low_returns is True
    assert any("below" in note for note in weak_assessment.notes)


def test_missing_statements_stay_neutral_and_financials_skip_altman():
    blank = assess_quality(StockFundamentals(ticker="EMPTY", price=10))
    assert blank.f_score_available == 0
    assert blank.altman_z is None
    assert blank.roic is None
    assert blank.bankruptcy_risk == "unknown"
    assert blank.quality_score == 50
    assert blank.low_returns is False

    bank = assess_quality(_healthy(), is_financial=True)
    assert bank.altman_z is None
    assert any("not applied" in note for note in bank.notes)

    illiquid = _healthy().model_copy(
        update={
            "total_assets": None,
            "total_assets_history": [],
            "retained_earnings": None,
            "current_ratio": 0.4,
            "current_ratio_history": [],
            "debt_to_equity": 2.0,
            "ebit": -5,
            "long_term_debt_history": [],
            "total_debt_history": [],
        }
    )
    assert assess_quality(illiquid).bankruptcy_risk == "distress"
    assert assess_quality(illiquid, is_financial=True).bankruptcy_risk == "unknown"


def test_a_cheap_high_quality_name_ranks_above_a_cheap_low_quality_name():
    settings = Settings()
    good = _stock(
        ticker="GOOD",
        total_assets=2_000_000_000,
        total_assets_history=[2_000_000_000, 1_800_000_000],
        total_equity=1_000_000_000,
        equity_history=[1_000_000_000, 900_000_000],
        net_income=150_000_000,
        net_income_history=[150_000_000, 120_000_000],
        operating_cash_flow=160_000_000,
        operating_cash_flow_history=[160_000_000, 120_000_000],
        revenue_history=[500_000_000, 450_000_000],
        ebit_history=[200_000_000, 180_000_000],
        retained_earnings=400_000_000,
        current_liabilities=160_000_000,
        shares_history=[20_000_000, 20_000_000],
        current_ratio_history=[2.5, 2.0],
        long_term_debt_history=[80_000_000, 120_000_000],
    )
    trap = _stock(
        ticker="TRAP",
        ebit=90_000_000,
        total_assets=2_000_000_000,
        total_assets_history=[2_000_000_000, 1_500_000_000],
        total_equity=1_000_000_000,
        equity_history=[1_000_000_000, 1_000_000_000],
        net_income=20_000_000,
        net_income_history=[20_000_000, 25_000_000],
        operating_cash_flow=5_000_000,
        operating_cash_flow_history=[5_000_000, 30_000_000],
        revenue_history=[500_000_000, 650_000_000],
        ebit_history=[90_000_000, 40_000_000],
        retained_earnings=50_000_000,
        current_liabilities=160_000_000,
        shares_history=[20_000_000, 15_000_000],
        current_ratio_history=[2.5, 3.0],
        long_term_debt_history=[100_000_000, 40_000_000],
        total_debt_history=[100_000_000, 40_000_000],
    )
    good_screen = screen_stock(good, compute_metrics(good, settings), settings)
    trap_screen = screen_stock(trap, compute_metrics(trap, settings), settings)
    good_record = build_record(good, good_screen, settings, aaa_yield=6)
    trap_record = build_record(trap, trap_screen, settings, aaa_yield=6)
    assert good_record.quality_score is not None and trap_record.quality_score is not None
    assert good_record.quality_score > trap_record.quality_score
    assert good_record.composite_score > trap_record.composite_score
    assert trap_record.low_returns is True
    assert good_record.signal == "Strong Value"
    assert trap_record.signal == "Watchlist"
    assert any("below the 40 minimum" in reason for reason in trap_record.reasons)
    ranked = rank_records([trap_record, good_record], min_mos=0.25, min_quality=40)
    assert [record.ticker for record in ranked] == ["GOOD"]
    watchlist = rank_records([trap_record, good_record], min_mos=0.25, signal="watchlist")
    assert [record.ticker for record in watchlist] == ["TRAP"]
