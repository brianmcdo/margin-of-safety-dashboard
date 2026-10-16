"""Point-in-time screens and equal-weight forward returns. No network."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.backtest.engine import add_months, price_return, qualifying_exit, run_historical_screen
from src.backtest.history import closes_from_frame
from src.config import Settings
from src.data.cache import SnapshotStore
from src.main import build_parser
from tests.fixtures import healthy_stock, missing_data_stock


AS_OF = date(2024, 1, 15)
SIX_MONTH = date(2024, 7, 15)
ONE_YEAR = date(2025, 1, 15)


def _at(stock, when: datetime, **overrides):
    payload = {"fetched_at": when}
    payload.update(overrides)
    return stock.model_copy(update=payload)


def test_add_months_clips_to_the_end_of_the_month():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 1, 15), 6) == SIX_MONTH
    assert add_months(date(2024, 1, 15), 12) == ONE_YEAR


def test_a_close_from_the_entry_week_is_not_a_six_month_exit():
    assert price_return(40, 50) == 0.25
    assert price_return(0, 50) is None
    horizon = SIX_MONTH
    assert qualifying_exit((date(2024, 7, 5), 50), entry_date=AS_OF, horizon=horizon) == (date(2024, 7, 5), 50)
    assert qualifying_exit((date(2024, 7, 4), 50), entry_date=AS_OF, horizon=horizon) is None
    assert qualifying_exit((AS_OF, 40), entry_date=AS_OF, horizon=horizon) is None


def test_screen_ignores_snapshots_and_prices_from_after_the_date(tmp_path):
    store = SnapshotStore(str(tmp_path / "signals.db"))
    early = _at(
        healthy_stock(),
        datetime(2024, 1, 15, tzinfo=timezone.utc),
        name="Early Co",
        price=40.0,
    )
    late = _at(
        healthy_stock(),
        datetime(2025, 6, 1, tzinfo=timezone.utc),
        name="Late Co",
        price=10.0,
    )
    future = _at(
        healthy_stock(ticker="FUTURE", name="Future Co"),
        datetime(2025, 6, 1, tzinfo=timezone.utc),
        price=15.0,
    )
    store.save_fundamentals(early)
    store.save_fundamentals(late)
    store.save_fundamentals(future)
    store.save_prices("VAL", [(date(2024, 3, 1), 80.0), (date(2025, 1, 1), 5.0)])

    seen = store.fundamentals_as_of(date(2024, 6, 1))
    assert [stock.ticker for stock in seen] == ["VAL"]
    assert seen[0].name == "Early Co"
    quote = store.price_on_or_before("VAL", date(2024, 6, 1))
    assert quote == (date(2024, 3, 1), 80.0)

    result = run_historical_screen(store, Settings(), date(2024, 6, 1), benchmark="SPY")
    assert result.summary.universe_size == 1
    assert result.records[0].ticker == "VAL"
    assert result.records[0].name == "Early Co"
    assert result.records[0].price == 80.0
    assert store.latest_run() is None


def test_forward_returns_compare_the_selection_with_the_universe(tmp_path):
    store = SnapshotStore(str(tmp_path / "signals.db"))
    when = datetime(2024, 1, 15, tzinfo=timezone.utc)
    store.save_fundamentals(_at(healthy_stock(), when))
    store.save_fundamentals(_at(missing_data_stock(), when))
    store.save_prices("VAL", [(SIX_MONTH, 50.0), (ONE_YEAR, 48.0)])
    store.save_prices("MISS", [(SIX_MONTH, 20.0), (ONE_YEAR, 25.0)])
    store.save_prices("SPY", [(AS_OF, 100.0), (SIX_MONTH, 110.0), (ONE_YEAR, 120.0)])

    result = run_historical_screen(store, Settings(), AS_OF, benchmark="SPY")
    summary = result.summary
    assert [row.ticker for row in result.selected] == ["VAL"]
    assert result.selected[0].return_6m == pytest.approx(0.25)
    assert result.selected[0].return_1y == pytest.approx(0.20)
    assert summary.selected_6m == pytest.approx(0.25)
    assert summary.universe_6m == pytest.approx((0.25 - 0.20) / 2)
    assert summary.excess_6m == pytest.approx(summary.selected_6m - summary.universe_6m)
    assert summary.benchmark_6m == pytest.approx(0.10)
    assert summary.benchmark_1y == pytest.approx(0.20)
    assert summary.priced_6m == 1
    assert summary.priced_1y == 1


def test_close_frame_reads_a_single_series_and_a_multiindex():
    single = pd.DataFrame({"Close": [10.0, 11.0]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    assert closes_from_frame(single, ["VAL"])["VAL"] == [(date(2024, 1, 2), 10.0), (date(2024, 1, 3), 11.0)]
    wide = pd.DataFrame(
        {("Close", "AAA"): [1.0, None], ("Close", "BBB"): [2.0, 3.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    parsed = closes_from_frame(wide, ["AAA", "BBB"])
    assert parsed["AAA"] == [(date(2024, 1, 2), 1.0)]
    assert parsed["BBB"] == [(date(2024, 1, 2), 2.0), (date(2024, 1, 3), 3.0)]


def test_cli_accepts_a_historical_screen():
    args = build_parser().parse_args(["--as-of", "2024-01-15", "--benchmark", "SPY", "--fetch-prices"])
    assert args.as_of == "2024-01-15"
    assert args.benchmark == "SPY"
    assert args.fetch_prices is True
    assert build_parser().parse_args([]).as_of is None
