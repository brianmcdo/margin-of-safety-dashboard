"""Headline flags stay offline. No Yahoo call."""

from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.data.models import NewsHeadline
from src.news.sentiment import assess_news, attach_headlines, headlines_from_articles
from src.screening.filters import compute_metrics, screen_stock
from src.signals.generator import build_record
from tests.fixtures import healthy_stock


def _record(stock, settings):
    screen = screen_stock(stock, compute_metrics(stock, settings), settings)
    return build_record(stock, screen, settings, aaa_yield=6)


def test_severe_headline_flags_without_changing_the_score():
    settings = Settings()
    stock = healthy_stock()
    plain = _record(stock, settings)
    stock.headlines = [NewsHeadline(title="Acme files for bankruptcy protection")]
    record = _record(stock, settings)
    assert record.negative_news is True
    assert record.news_headlines == ["Acme files for bankruptcy protection"]
    assert record.composite_score == plain.composite_score
    assert "bankruptcy protection" in record.reasons[-1]


def test_downrank_subtracts_the_penalty_and_floors_at_zero():
    settings = Settings()
    settings.news.downrank = True
    settings.news.penalty = 8
    stock = healthy_stock()
    base = _record(stock, settings).composite_score
    stock.headlines = [NewsHeadline(title="Fraud probe widens")]
    record = _record(stock, settings)
    assert record.composite_score == round(base - 8, 2)
    assert "reduced by 8" in record.reasons[-1]

    settings.news.penalty = 500
    floored = _record(stock, settings)
    assert floored.composite_score == 0


def test_one_downgrade_is_not_enough_and_two_mild_headlines_are():
    settings = Settings()
    stock = healthy_stock()
    stock.headlines = [NewsHeadline(title="Analyst downgrade after a quiet quarter")]
    assert assess_news(stock, settings.news).flagged is False
    stock.headlines.append(NewsHeadline(title="Company announces layoffs at the plant"))
    result = assess_news(stock, settings.news)
    assert result.flagged is True
    assert len(result.headlines) == 2


def test_a_positive_word_offsets_a_severe_title():
    settings = Settings()
    stock = healthy_stock()
    stock.headlines = [NewsHeadline(title="Acme beats bankruptcy fears and soars")]
    assert assess_news(stock, settings.news).flagged is False


def test_a_headline_older_than_the_lookback_is_ignored():
    settings = Settings()
    settings.news.lookback_days = 14
    stock = healthy_stock()
    stock.fetched_at = datetime(2026, 10, 15, tzinfo=timezone.utc)
    stock.headlines = [
        NewsHeadline(
            title="Fraud investigation",
            published_at=stock.fetched_at - timedelta(days=40),
        )
    ]
    assert assess_news(stock, settings.news).flagged is False


def test_turning_the_check_off_ignores_headlines():
    settings = Settings()
    settings.news.enabled = False
    stock = healthy_stock()
    stock.headlines = [NewsHeadline(title="Company files for bankruptcy")]
    assert assess_news(stock, settings.news).flagged is False


def test_both_yahoo_news_shapes_parse():
    parsed = headlines_from_articles(
        [
            {"title": "Old shape", "providerPublishTime": 1_700_000_000},
            {"content": {"title": "New shape", "pubDate": "2026-10-01T12:00:00Z"}},
            {"ad": True, "title": "Sponsored"},
        ]
    )
    assert [item.title for item in parsed] == ["Old shape", "New shape"]
    assert parsed[0].published_at is not None
    assert parsed[1].published_at is not None


def test_a_news_failure_leaves_the_stock_unchanged(monkeypatch):
    def boom(ticker, limit):
        raise RuntimeError(f"{ticker} {limit} down")

    monkeypatch.setattr("src.news.sentiment.fetch_headlines", boom)
    stock = healthy_stock()
    updated = attach_headlines(stock, Settings().news)
    assert updated.headlines == []
    assert updated.ticker == stock.ticker
