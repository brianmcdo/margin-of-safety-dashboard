"""Cache window, rate-limit retries, FMP credentials, and the FRED yield."""

from datetime import timedelta

from src.config import Settings
from src.data.cache import SnapshotStore
from src.data.fetcher import (
    _is_rate_limit,
    fetch_aaa_yield,
    fetch_universe,
    resolve_fmp_api_key,
    resolve_source,
)
from src.data.models import utcnow
from tests.test_filters_signals import _stock


def _quiet(settings: Settings) -> Settings:
    settings.data.max_retries = 2
    settings.data.retry_backoff_seconds = 0
    settings.data.rate_limit_backoff_seconds = 0
    settings.data.fmp_api_key = ""
    settings.universe.cache_ttl_hours = 24
    settings.news.enabled = False
    return settings


def test_fmp_key_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "from-env")
    settings = Settings()
    settings.data.fmp_api_key = "from-config"
    assert resolve_fmp_api_key(settings) == "from-env"


def test_fmp_key_falls_back_to_config_and_selects_auto_source(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    settings = Settings()
    settings.data.fmp_api_key = "from-config"
    settings.data.source = "auto"
    assert resolve_fmp_api_key(settings) == "from-config"
    assert resolve_source(settings) == "fmp"
    settings.data.fmp_api_key = ""
    assert resolve_source(settings) == "yfinance"


def test_rate_limit_errors_are_recognized():
    assert _is_rate_limit(RuntimeError("429 Too Many Requests"))
    assert _is_rate_limit(RuntimeError("Yahoo rate limit"))

    class YFRateLimitError(Exception):
        pass

    assert _is_rate_limit(YFRateLimitError("slow down"))
    assert not _is_rate_limit(RuntimeError("no price returned"))


def test_fresh_cache_skips_the_network(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)

    def boom(ticker):
        raise AssertionError(f"should not fetch {ticker}")

    monkeypatch.setattr("src.data.fetcher.fetch_yfinance", boom)
    settings = _quiet(Settings())
    store = SnapshotStore(str(tmp_path / "signals.db"))
    store.save_fundamentals(_stock())
    stocks, failures = fetch_universe(["VAL"], settings, store)
    assert failures == []
    assert [item.ticker for item in stocks] == ["VAL"]


def test_rate_limit_retries_then_uses_fmp(monkeypatch, tmp_path):
    monkeypatch.setattr("src.data.fetcher._sleep", lambda _seconds: None)
    calls: list[str] = []

    def yfinance_limited(ticker):
        calls.append(f"yfinance:{ticker}")
        raise RuntimeError("429 Too Many Requests")

    def fmp_ok(ticker, settings):
        calls.append(f"fmp:{ticker}")
        return _stock(ticker=ticker, source="fmp")

    monkeypatch.setattr("src.data.fetcher.fetch_yfinance", yfinance_limited)
    monkeypatch.setattr("src.data.fetcher.fetch_fmp", fmp_ok)
    settings = _quiet(Settings())
    settings.data.fmp_api_key = "test-key"
    store = SnapshotStore(str(tmp_path / "signals.db"))
    stocks, failures = fetch_universe(["VAL"], settings, store)
    assert failures == []
    assert stocks[0].source == "fmp"
    assert calls == ["yfinance:VAL", "yfinance:VAL", "fmp:VAL"]


def test_failed_fetch_uses_an_expired_snapshot(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr("src.data.fetcher._sleep", lambda _seconds: None)

    def boom(ticker):
        raise RuntimeError(f"no price returned for {ticker}")

    monkeypatch.setattr("src.data.fetcher.fetch_yfinance", boom)
    settings = _quiet(Settings())
    settings.data.max_retries = 1
    store = SnapshotStore(str(tmp_path / "signals.db"))
    stale = _stock().model_copy(update={"fetched_at": utcnow() - timedelta(hours=30)})
    store.save_fundamentals(stale)
    stocks, failures = fetch_universe(["VAL"], settings, store)
    assert failures == []
    assert "live fetch failed" in stocks[0].notes[-1]
    assert "no price returned for VAL" in stocks[0].notes[-1]


def test_aaa_yield_uses_the_cache_then_the_configured_default(monkeypatch, tmp_path):
    monkeypatch.setattr("src.data.fetcher._sleep", lambda _seconds: None)
    store = SnapshotStore(str(tmp_path / "signals.db"))
    store.set_meta("fred:DAAA", "5.50")

    def unexpected(url, timeout):
        raise AssertionError(url)

    monkeypatch.setattr("src.data.fetcher._http_get", unexpected)
    settings = _quiet(Settings())
    assert fetch_aaa_yield(settings, store) == 5.50

    empty = SnapshotStore(str(tmp_path / "empty.db"))

    def offline(url, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("src.data.fetcher._http_get", offline)
    settings.fred.fallback_aaa_yield = 6.0
    assert fetch_aaa_yield(settings, empty) == 6.0
