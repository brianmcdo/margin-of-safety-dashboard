"""Parsing helpers, the local snapshot store, and an offline pipeline run."""

from datetime import timedelta

import pandas as pd

from src.config import Settings, load_settings
from src.data.cache import SnapshotStore
from src.data.models import utcnow
from src.data.parse import (
    annual_series,
    assemble_fundamentals,
    coerce_debt_to_equity,
    latest_line_item,
)
from src.data.fetcher import _last_fred_value
from src.data.universe import normalize_ticker
from src.pipeline import run_pipeline
from src.signals.generator import STRONG
from tests.test_filters_signals import _stock


def test_config_file_loads_the_conservative_defaults():
    settings = load_settings()
    assert settings.valuation.mos.minimum == 0.25
    assert settings.valuation.dcf.discount_rate == 0.10
    assert settings.screens.thresholds.max_pe == 15


def test_yahoo_debt_to_equity_percentage_is_normalized_unless_statements_exist():
    assert coerce_debt_to_equity(15.921, reported_is_percent=True, total_debt=None, book_equity=None) == 15.921 / 100
    # Statement totals win. 40 of debt on 200 of equity is 0.20, not 15.9%.
    assert coerce_debt_to_equity(15.921, reported_is_percent=True, total_debt=40, book_equity=200) == 0.2


def test_statement_aliases_read_the_latest_finite_value():
    frame = pd.DataFrame(
        {"2024": [10.0, float("nan")], "2023": [8.0, 4.0]},
        index=["Current Assets", "Free Cash Flow"],
    ).T
    # After transpose the index is the year labels, which is the shape some
    # vendors return. The helper puts line items back on the index.
    frame.index = pd.to_datetime(frame.index)
    assert latest_line_item(frame, ["Current Assets"]) == 10
    assert annual_series(frame, ["Free Cash Flow"]) == [4]


def test_assemble_derives_book_value_and_market_cap():
    stock = assemble_fundamentals(
        ticker="brk.b",
        price=100,
        shares_outstanding=10,
        total_equity=250,
        debt_to_equity_reported=50,
        debt_to_equity_is_percent=True,
        total_debt=None,
    )
    assert stock.ticker == "BRK-B"
    assert stock.book_value_per_share == 25
    assert stock.market_cap == 1000
    assert stock.debt_to_equity == 0.5


def test_fred_parser_skips_missing_observations():
    csv_text = "DATE,DAAA\n2026-01-01,.\n2026-01-02,6.25\n"
    assert _last_fred_value(csv_text) == 6.25


def test_class_share_tickers_use_yahoo_dashes():
    assert normalize_ticker("brk.b") == "BRK-B"


def test_cache_round_trip_respects_ttl(tmp_path):
    store = SnapshotStore(str(tmp_path / "signals.db"))
    stock = _stock()
    store.save_fundamentals(stock)
    assert store.get_fundamentals("VAL", ttl_hours=20) is not None
    stale = stock.model_copy(update={"fetched_at": utcnow() - timedelta(hours=48)})
    store.save_fundamentals(stale)
    assert store.get_fundamentals("VAL", ttl_hours=20) is None


def test_pipeline_values_a_prepared_universe_without_the_network(tmp_path):
    settings = Settings()
    settings.data.sqlite_path = str(tmp_path / "signals.db")
    bargain = _stock()
    result = run_pipeline(
        settings,
        universe="fixture",
        fundamentals=[bargain],
        aaa_yield=6,
    )
    assert result.failures == []
    assert result.records[0].signal == STRONG
    reloaded = SnapshotStore(settings.data.sqlite_path).latest_run()
    assert reloaded is not None
    assert reloaded.records[0].ticker == "VAL"
    assert reloaded.aaa_yield == 6
