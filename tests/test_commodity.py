"""Commodity detection, planning-price NAV, and the low-cost flag. No network."""

import pytest

from src.config import Settings
from src.screening.filters import compute_metrics, screen_stock
from src.signals.generator import build_record
from src.valuation.commodity import (
    cash_flow_scale,
    classify_producer,
    commodity_nav_per_share,
    commodity_sensitivity,
    is_commodity_producer,
    is_low_cost_producer,
)
from src.valuation.dcf import conservative_dcf_per_share, normalized_free_cash_flow
from tests.fixtures import healthy_stock, high_debt_stock


def test_sector_or_industry_detects_energy_and_mining_and_skips_utilities():
    settings = Settings().commodity
    oil = healthy_stock(sector="Energy", industry="Refining")
    assert classify_producer(oil, settings).commodity == "oil"
    assert classify_producer(oil, settings).matched == "sector"
    gold = healthy_stock(sector="Basic Materials", industry="Gold")
    assert classify_producer(gold, settings).commodity == "gold"
    copper = healthy_stock(sector="Basic Materials", industry="Copper")
    assert classify_producer(copper, settings).commodity == "copper"
    assert not is_commodity_producer(
        healthy_stock(sector="Utilities", industry="Utilities - Regulated Gas"),
        settings,
    )
    assert not is_commodity_producer(healthy_stock(), settings)


def test_planning_price_scales_cash_flow_and_the_table_uses_dollar_prices():
    settings = Settings()
    stock = healthy_stock(sector="Energy", industry="Oil & Gas E&P")
    quote = settings.commodity.prices["oil"]
    assert cash_flow_scale(quote.normalized, quote.reference) == pytest.approx(70 / 80)
    nav, note = commodity_nav_per_share(stock, settings.commodity, settings.valuation.dcf)
    base_fcf, _cash_note = normalized_free_cash_flow(
        stock.free_cash_flow,
        stock.fcf_history,
        settings.valuation.dcf.normalize_years,
    )
    manual, _detail = conservative_dcf_per_share(
        base_fcf * (70 / 80),
        stock.shares_outstanding,
        growth_rate=settings.valuation.dcf.growth_rate,
        years=settings.valuation.dcf.years,
        discount_rate=settings.valuation.dcf.discount_rate,
        terminal_growth=settings.valuation.dcf.terminal_growth,
        max_terminal_share=settings.valuation.dcf.max_terminal_share,
    )
    assert nav == pytest.approx(manual)
    assert "70" in note and "80" in note
    cells = commodity_sensitivity(
        stock,
        settings.commodity,
        settings.valuation.dcf,
        normalized_overrides={"oil": 60},
    )
    assert len(cells) == 5 * 3
    base = next(cell for cell in cells if cell.price_multiplier == 1 and cell.discount_rate == 0.10)
    assert base.commodity_price == pytest.approx(60)
    cheaper = next(cell for cell in cells if cell.price_multiplier == 0.70 and cell.discount_rate == 0.10)
    assert cheaper.commodity_price == pytest.approx(42)
    assert cheaper.value_per_share < base.value_per_share


def test_low_cost_flag_requires_quality_cash_flow_and_modest_debt():
    settings = Settings()
    producer = healthy_stock(ticker="MINE", sector="Basic Materials", industry="Gold")
    record = build_record(
        producer,
        screen_stock(producer, compute_metrics(producer, settings), settings),
        settings,
        aaa_yield=6,
    )
    assert record.commodity_name == "gold"
    assert record.low_cost_producer is True
    assert any("Low-cost producer" in reason for reason in record.reasons)
    assert record.commodity_nav is not None
    levered = high_debt_stock()
    levered = healthy_stock(
        ticker="DEBT",
        sector="Energy",
        industry="Oil & Gas E&P",
        total_debt=levered.total_debt,
        debt_to_equity=8.0,
    )
    levered_record = build_record(
        levered,
        screen_stock(levered, compute_metrics(levered, settings), settings),
        settings,
        aaa_yield=6,
    )
    assert levered_record.is_commodity_producer is True
    assert levered_record.low_cost_producer is False
    assert is_low_cost_producer(
        is_producer=True,
        quality_score=80,
        low_returns=False,
        bankruptcy_risk="safe",
        free_cash_flow=-1,
        debt_to_equity=0.2,
        settings=settings.commodity,
    ) is False
