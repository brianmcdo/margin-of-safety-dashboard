"""Keyword sentiment on recent headlines.

This is a short word list, not a language model. A headline with no listed
word is ignored. One severe word (bankruptcy, fraud, a criminal charge) flags
the name. Milder words, such as a downgrade or a layoff, flag it only when at
least two recent headlines are negative. A positive word in the same title
offsets those hits.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.config import NewsSettings
from src.data.models import NewsHeadline, StockFundamentals

logger = logging.getLogger(__name__)

# Severe words flag a name on their own. Moderate words need a second negative headline.
_SEVERE = (
    "bankruptcy",
    "bankrupt",
    "fraud",
    "fraudulent",
    "indictment",
    "indicted",
    "insolvency",
    "insolvent",
    "delisting",
    "delisted",
    "scandal",
    "criminal",
)
_MODERATE = (
    "lawsuit",
    "sued",
    "investigation",
    "probe",
    "recall",
    "plunge",
    "plunges",
    "plunged",
    "default",
    "defaults",
    "layoff",
    "layoffs",
    "downgrade",
    "downgraded",
    "warning",
    "liquidation",
    "restatement",
    "restated",
    "bribery",
    "halted",
    "collapse",
    "collapsed",
    "slump",
    "shortfall",
    "misses",
    "missed",
    "guidance cut",
    "cuts guidance",
    "cut guidance",
    "profit warning",
    "sec charges",
)
_POSITIVE = (
    "beats",
    "beat estimates",
    "upgrade",
    "upgraded",
    "record profit",
    "raises guidance",
    "raised guidance",
    "dividend increase",
    "buyback",
    "surges",
    "soars",
)


@dataclass(frozen=True)
class NewsAssessment:
    flagged: bool = False
    headlines: list[str] = field(default_factory=list)
    note: str = ""


def assess_news(
    stock: StockFundamentals,
    settings: NewsSettings,
    *,
    as_of: datetime | None = None,
) -> NewsAssessment:
    """Flag very negative headlines stored on the stock. No network call."""
    if not settings.enabled or not stock.headlines:
        return NewsAssessment()
    moment = _aware(as_of or stock.fetched_at)
    cutoff = moment - timedelta(days=max(settings.lookback_days, 0))
    severe_titles: list[str] = []
    mild_titles: list[str] = []
    for headline in stock.headlines:
        title = headline.title.strip()
        if not title:
            continue
        published = _aware(headline.published_at) if headline.published_at else None
        if published is not None and published < cutoff:
            continue
        kind = _headline_kind(title)
        if kind == "severe":
            severe_titles.append(title)
        elif kind == "mild":
            mild_titles.append(title)
    negative = list(severe_titles)
    if len(mild_titles) >= max(settings.min_negative_headlines, 1):
        negative.extend(mild_titles)
    if not negative:
        return NewsAssessment()
    shown = "; ".join(f'"{title}"' for title in negative[:2])
    extra = len(negative) - 2
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return NewsAssessment(
        flagged=True,
        headlines=negative,
        note=(
            f"Very negative recent headlines: {shown}{suffix}. "
            "Keyword check on Yahoo titles, not a language model."
        ),
    )


def headlines_from_articles(articles: list[dict]) -> list[NewsHeadline]:
    """Read titles from either Yahoo news shape yfinance has returned."""
    parsed: list[NewsHeadline] = []
    for article in articles:
        if not isinstance(article, dict) or article.get("ad"):
            continue
        content = article.get("content")
        source = content if isinstance(content, dict) else article
        title = source.get("title") or article.get("title") or ""
        if not isinstance(title, str) or not title.strip():
            continue
        published = _published_at(
            source.get("pubDate")
            or source.get("displayTime")
            or article.get("providerPublishTime")
        )
        parsed.append(NewsHeadline(title=title.strip(), published_at=published))
    return parsed


def fetch_headlines(ticker: str, limit: int) -> list[NewsHeadline]:
    """Recent Yahoo titles for one ticker. Raises if the request fails."""
    import yfinance as yf

    raw = yf.Ticker(ticker).get_news(count=max(1, limit))
    if not isinstance(raw, list):
        return []
    return headlines_from_articles(raw)[: max(1, limit)]


def attach_headlines(stock: StockFundamentals, settings: NewsSettings) -> StockFundamentals:
    """Add headlines when Yahoo answers. A news failure leaves the stock unchanged."""
    if not settings.enabled:
        return stock
    try:
        headlines = fetch_headlines(stock.ticker, settings.max_headlines)
    except Exception as exc:
        logger.warning("%s news unavailable: %s", stock.ticker, exc.__class__.__name__)
        return stock
    if not headlines:
        return stock
    return stock.model_copy(update={"headlines": headlines})


def _headline_kind(title: str) -> str:
    """Return 'severe', 'mild', or '' after positive words offset the hits."""
    severe = _count(title, _SEVERE)
    moderate = _count(title, _MODERATE)
    positive = _count(title, _POSITIVE)
    if severe == 0 and moderate == 0:
        return ""
    if positive > 0 and positive >= severe + moderate:
        return ""
    if severe > 0 and (positive == 0 or severe > positive):
        return "severe"
    if moderate > 0 and positive == 0:
        return "mild"
    return ""


def _count(title: str, phrases: tuple[str, ...]) -> int:
    text = title.lower()
    return sum(1 for phrase in phrases if re.search(rf"\b{re.escape(phrase)}\b", text))


def _published_at(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _aware(parsed)
    return None


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
