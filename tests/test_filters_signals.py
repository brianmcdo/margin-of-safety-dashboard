"""Screens, scores, and signal labels."""

from src.config import Settings
from src.data.models import StockFundamentals
from src.screening.filters import compute_metrics, is_financial, screen_stock
from src.signals.generator import (
    DEEP,
    MODERATE,
    NONE,
    STRONG,
    WATCHLIST,
    build_record,
    classify_signal,
    rank_records,
)
from src.valuation.commodity import is_commodity_producer
from src.valuation.scoring import composite_score, margin_of_safety


def _stock(**overrides) -> StockFundamentals:
    payload = dict(
        ticker="VAL",
        name="Value Co",
        sector="Industrials",
        industry="Specialty Industrial Machinery",
        price=40.0,
        market_cap=800_000_000.0,
        shares_outstanding=20_000_000.0,
        trailing_eps=5.0,
        book_value_per_share=50.0,
        current_assets=400_000_000.0,
        total_liabilities=200_000_000.0,
        total_debt=100_000_000.0,
        cash=50_000_000.0,
        free_cash_flow=100_000_000.0,
        fcf_history=[90_000_000.0, 100_000_000.0, 110_000_000.0],
        revenue=500_000_000.0,
        ebit=200_000_000.0,
        current_ratio=2.5,
        debt_to_equity=0.10,
        average_volume=1_000_000.0,
    )
    payload.update(overrides)
    return StockFundamentals(**payload)


def test_margin_of_safety_is_the_discount_to_intrinsic_value():
    assert margin_of_safety(100, 50) == 0.5
    assert margin_of_safety(50, 80) == -0.6
    assert margin_of_safety(0, 10) is None
    assert margin_of_safety(-5, 10) is None


def test_composite_score_weights_a_clear_bargain():
    stock = _stock()
    metrics = compute_metrics(stock, Settings())
    metrics.pe = 7
    metrics.pb = 0.7
    metrics.ev_ebit = 5
    metrics.debt_to_equity = 0.2
    metrics.current_ratio = 2.2
    score = composite_score(stock, metrics, 0.40, Settings().scoring.weights)
    assert score == 87.5


def test_quality_stock_at_a_discount_is_strong_value():
    settings = Settings()
    stock = _stock()
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    assert screen.passed
    record = build_record(stock, screen, settings, aaa_yield=6)
    assert record.signal == STRONG
    assert record.graham_number == (22.5 * 5 * 50) ** 0.5
    assert record.margin_of_safety is not None and record.margin_of_safety >= 0.40
    assert record.composite_score > 70


def test_net_net_without_earnings_is_deep_value():
    settings = Settings()
    shares = 100_000_000.0
    stock = _stock(
        ticker="NET",
        price=8.0,
        shares_outstanding=shares,
        market_cap=8.0 * shares,
        trailing_eps=-1.0,
        book_value_per_share=None,
        free_cash_flow=-10_000_000.0,
        fcf_history=[],
        current_assets=20.0 * shares,
        total_liabilities=5.0 * shares,
        total_debt=None,
        cash=None,
        ebit=None,
        current_ratio=None,
        debt_to_equity=None,
        average_volume=500_000.0,
    )
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    assert screen.net_net
    assert screen.passed
    record = build_record(stock, screen, settings, aaa_yield=6)
    assert record.signal == DEEP
    assert any("below NCAV" in reason for reason in record.reasons)


def test_expensive_stock_is_not_a_signal():
    settings = Settings()
    stock = _stock(ticker="GROW", price=200, trailing_eps=4, book_value_per_share=20, ebit=50_000_000)
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    assert not screen.passed
    record = build_record(stock, screen, settings, aaa_yield=6)
    assert record.signal == NONE


def test_banks_are_not_labeled_net_nets():
    settings = Settings()
    stock = _stock(sector="Financial Services", industry="Banks - Diversified", price=5)
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    assert is_financial(stock)
    assert not screen.net_net
    record = build_record(stock, screen, settings, aaa_yield=6)
    assert record.ncav_per_share is None


def test_classify_signal_downgrades_disagreement_and_keeps_a_thin_discount_on_the_watchlist():
    assert (
        classify_signal(
            passed_screen=True,
            liquidity_ok=True,
            net_net=False,
            earnings_support=True,
            median_mos=0.45,
            min_method_mos=-0.05,
            strong_threshold=0.40,
            moderate_threshold=0.25,
        )
        == MODERATE
    )
    assert (
        classify_signal(
            passed_screen=True,
            liquidity_ok=True,
            net_net=False,
            earnings_support=False,
            median_mos=0.10,
            min_method_mos=0.05,
            strong_threshold=0.40,
            moderate_threshold=0.25,
        )
        == WATCHLIST
    )


def test_ranked_table_requires_the_margin_floor_and_sorts_by_score():
    settings = Settings()
    strong = build_record(_stock(ticker="AAA"), screen_stock(_stock(ticker="AAA"), compute_metrics(_stock(ticker="AAA"), settings), settings), settings, 6)
    pricey = build_record(
        _stock(ticker="ZZZ", price=200, trailing_eps=4, book_value_per_share=20),
        screen_stock(
            _stock(ticker="ZZZ", price=200, trailing_eps=4, book_value_per_share=20),
            compute_metrics(_stock(ticker="ZZZ", price=200, trailing_eps=4, book_value_per_share=20), settings),
            settings,
        ),
        settings,
        6,
    )
    ranked = rank_records([pricey, strong], min_mos=0.30)
    assert [record.ticker for record in ranked] == ["AAA"]
    assert pricey.signal == NONE


def test_commodity_flag_matches_producers_and_skips_utilities():
    settings = Settings()
    assert is_commodity_producer(_stock(sector="Energy", industry="Oil & Gas Integrated"), settings.commodity)
    assert is_commodity_producer(_stock(sector="Basic Materials", industry="Gold"), settings.commodity)
    assert not is_commodity_producer(
        _stock(sector="Utilities", industry="Utilities - Regulated Gas"),
        settings.commodity,
    )
    assert not is_commodity_producer(_stock(), settings.commodity)
