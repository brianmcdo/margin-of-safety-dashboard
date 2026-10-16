"""Dashboard API. Uses a temporary database and does not call the network."""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.api.server import app
from src.config import Settings
from src.data.cache import SnapshotStore
from src.data.models import PipelineResult, SignalRecord, utcnow
from tests.fixtures import healthy_stock


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = Settings()
    settings.data.sqlite_path = str(tmp_path / "signals.db")
    settings.logging.file = str(tmp_path / "test.log")
    monkeypatch.setattr("src.api.server._settings", lambda: settings)
    store = SnapshotStore(settings.data.sqlite_path)
    return TestClient(app), store


def test_health_and_an_empty_snapshot(api):
    client, _store = api
    assert client.get("/api/health").json() == {"status": "ok"}
    body = client.get("/api/snapshot").json()
    assert body["run"] is None
    assert body["records"] == []
    assert body["context"]["portfolio"]["max_position_size"] == 0.10
    assert body["context"]["mos_minimum"] == 0.30
    span = client.get("/api/history/span").json()
    assert span == {"first": None, "last": None}


def test_snapshot_returns_the_saved_run_and_refreshes_a_low_cost_flag(api):
    client, store = api
    mine = healthy_stock(ticker="MINE", sector="Basic Materials", industry="Gold")
    store.save_fundamentals(mine)
    store.save_run(
        PipelineResult(
            run_id="run-1",
            created_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
            universe="sample",
            aaa_yield=6.0,
            records=[
                SignalRecord(
                    ticker="MINE",
                    name="Mine Co",
                    sector="Basic Materials",
                    signal="Strong Value",
                    composite_score=80,
                    margin_of_safety=0.42,
                    is_commodity_producer=True,
                    low_cost_producer=False,
                )
            ],
        )
    )
    body = client.get("/api/snapshot").json()
    assert body["run"]["run_id"] == "run-1"
    assert body["run"]["universe"] == "sample"
    assert body["run"]["aaa_yield"] == 6.0
    assert body["records"][0]["ticker"] == "MINE"
    assert body["records"][0]["low_cost_producer"] is True


def test_portfolio_uses_the_requested_order_and_rejects_a_bad_cap(api):
    client, store = api
    store.save_run(
        PipelineResult(
            run_id="run-1",
            created_at=utcnow(),
            universe="sample",
            aaa_yield=6.0,
            records=[
                SignalRecord(ticker="E0", name="Energy Co", sector="Energy", signal="Strong Value", composite_score=90),
                SignalRecord(ticker="H0", name="Health Co", sector="Healthcare", signal="Strong Value", composite_score=70),
            ],
        )
    )
    basket = client.post(
        "/api/portfolio",
        json={
            "tickers": ["H0", "E0"],
            "max_sector_weight": 0.25,
            "max_position_size": 0.10,
            "min_holdings": 2,
        },
    ).json()
    assert [row["ticker"] for row in basket["holdings"]] == ["H0", "E0"]
    assert basket["invested"] == pytest.approx(0.20)
    assert any("no further names" in note for note in basket["notes"])

    rejected = client.post(
        "/api/portfolio",
        json={"tickers": ["E0"], "max_sector_weight": 0, "max_position_size": 0.10, "min_holdings": 1},
    )
    assert rejected.status_code == 400


def test_commodity_grid_follows_the_planning_price(api):
    client, store = api
    store.save_fundamentals(healthy_stock(ticker="XOM", sector="Energy", industry="Oil & Gas E&P"))
    store.save_fundamentals(healthy_stock(ticker="VAL"))
    plain = client.post("/api/commodity", json={"ticker": "VAL", "oil": 70, "gold": 2000, "copper": 4}).json()
    assert plain["producer"] is False

    cheap = client.post("/api/commodity", json={"ticker": "XOM", "oil": 70, "gold": 2000, "copper": 4}).json()
    rich = client.post("/api/commodity", json={"ticker": "XOM", "oil": 140, "gold": 2000, "copper": 4}).json()
    assert cheap["producer"] is True
    assert cheap["unit"]
    assert cheap["cells"]
    assert rich["nav"] > cheap["nav"]


def test_historical_screen_reads_the_stored_day_and_does_not_download(api, monkeypatch):
    client, store = api

    def boom(*_args, **_kwargs):
        raise AssertionError("the historical screen should not download prices")

    monkeypatch.setattr("src.api.server.fetch_closes", boom)
    when = datetime(2024, 1, 15, tzinfo=timezone.utc)
    store.save_fundamentals(healthy_stock().model_copy(update={"fetched_at": when}))
    store.save_prices("VAL", [(date(2024, 7, 15), 50.0), (date(2025, 1, 15), 48.0)])

    span = client.get("/api/history/span").json()
    assert span["first"] == "2024-01-15"
    payload = {
        "as_of": "2024-01-15",
        "min_mos": 0.30,
        "min_quality": 40,
        "signal": None,
        "benchmark": "SPY",
        "fetch_prices": False,
    }
    body = client.post("/api/history", json=payload).json()
    assert body["summary"]["universe_size"] == 1
    assert body["selected"][0]["ticker"] == "VAL"
    assert body["selected"][0]["entry_price"] == 40.0
    assert body["selected"][0]["return_6m"] == pytest.approx(0.25)
    assert body["summary"]["benchmark_ticker"] == "SPY"

    downloaded: list[list[str]] = []

    def fake_closes(tickers, _start, _end):
        downloaded.append(list(tickers))
        return {}

    monkeypatch.setattr("src.api.server.fetch_closes", fake_closes)
    payload["fetch_prices"] = True
    client.post("/api/history", json=payload)
    assert downloaded == [["VAL", "SPY"]]


def test_scan_reports_the_pipeline_result_and_a_failure(api, monkeypatch):
    client, _store = api
    calls: list[tuple] = []

    def fake(_settings, *, universe, refresh, limit, **_kwargs):
        calls.append((universe, refresh, limit))
        return PipelineResult(
            run_id="scan-1",
            created_at=utcnow(),
            universe=universe,
            aaa_yield=6.0,
            records=[SignalRecord(ticker="VAL", signal="Strong Value", composite_score=70)],
        )

    monkeypatch.setattr("src.api.server.run_pipeline", fake)
    response = client.post("/api/scan", json={"universe": " INTC,XOM ", "limit": 0, "refresh": True})
    assert response.status_code == 200
    assert response.json()["valued"] == 1
    assert calls == [("INTC,XOM", True, None)]

    def broken(*_args, **_kwargs):
        raise RuntimeError("no prices")

    monkeypatch.setattr("src.api.server.run_pipeline", broken)
    failed = client.post("/api/scan", json={"universe": "sample"})
    assert failed.status_code == 400
    assert failed.json()["detail"] == "no prices"
