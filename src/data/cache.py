"""SQLite cache for fundamentals, universe lists, and signal runs."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.data.models import FetchFailure, PipelineResult, SignalRecord, StockFundamentals, utcnow
from src.data.universe import normalize_ticker

logger = logging.getLogger(__name__)


class SnapshotStore:
    """Local snapshot database. Connections are opened per call."""

    def __init__(self, path: str) -> None:
        self.path = path
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fundamentals (
                    ticker TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ticker_lists (
                    name TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    universe TEXT NOT NULL,
                    summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    composite_score REAL NOT NULL,
                    margin_of_safety REAL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, ticker)
                );
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    snapshot_date TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    strong_value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pit_fundamentals (
                    as_of_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    price REAL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (as_of_date, ticker)
                );
                CREATE TABLE IF NOT EXISTS pit_prices (
                    ticker TEXT NOT NULL,
                    price_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    PRIMARY KEY (ticker, price_date)
                );
                """
            )
            self._backfill_point_in_time()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_fundamentals(
        self,
        ticker: str,
        ttl_hours: float,
        *,
        allow_expired: bool = False,
    ) -> StockFundamentals | None:
        """Return a cached record. Freshness is measured from ``fetched_at``.

        Within ``ttl_hours`` the row is fresh. Older rows are returned only
        when ``allow_expired`` is set, which is the fallback after a live
        fetch fails.
        """
        key = normalize_ticker(ticker)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM fundamentals WHERE ticker = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        fetched_at = _aware(row["fetched_at"])
        age = utcnow() - fetched_at
        expired = age > timedelta(hours=ttl_hours)
        if expired and not allow_expired:
            logger.info(
                "Cache expired for %s (fetched %s, %.1f hours old, TTL %.1f hours)",
                key,
                fetched_at.isoformat(),
                age.total_seconds() / 3600,
                ttl_hours,
            )
            return None
        try:
            stock = StockFundamentals.model_validate_json(row["payload"])
        except Exception:
            logger.exception("Cached fundamentals for %s could not be read; ignoring the row", key)
            return None
        if expired:
            logger.warning(
                "Using expired cache for %s fetched at %s (%.1f hours old)",
                key,
                fetched_at.isoformat(),
                age.total_seconds() / 3600,
            )
        return stock

    def save_fundamentals(self, stock: StockFundamentals) -> None:
        payload = stock.model_dump_json()
        fetched_at = stock.fetched_at.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fundamentals (ticker, fetched_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    payload = excluded.payload
                """,
                (normalize_ticker(stock.ticker), fetched_at, payload),
            )
            _write_pit_row(conn, normalize_ticker(stock.ticker), fetched_at, payload, stock.price)

    def get_ticker_list(self, name: str, ttl_hours: float) -> list[str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM ticker_lists WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        fetched_at = _aware(row["fetched_at"])
        if utcnow() - fetched_at > timedelta(hours=ttl_hours):
            return None
        payload = json.loads(row["payload"])
        if not isinstance(payload, list):
            return None
        return [str(item) for item in payload]

    def save_ticker_list(self, name: str, tickers: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ticker_lists (name, fetched_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    payload = excluded.payload
                """,
                (name, utcnow().isoformat(), json.dumps(tickers)),
            )

    def get_meta(self, key: str, ttl_hours: float) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, fetched_at FROM meta WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        fetched_at = _aware(row["fetched_at"])
        if utcnow() - fetched_at > timedelta(hours=ttl_hours):
            return None
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    fetched_at = excluded.fetched_at
                """,
                (key, value, utcnow().isoformat()),
            )

    def save_run(self, result: PipelineResult) -> None:
        summary = json.dumps(
            {
                "universe_size": len(result.records) + len(result.failures),
                "signals": len(result.records),
                "failures": [item.model_dump() for item in result.failures],
                "aaa_yield": result.aaa_yield,
            }
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, created_at, universe, summary) VALUES (?, ?, ?, ?)",
                (result.run_id, result.created_at.isoformat(), result.universe, summary),
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO signals
                    (run_id, ticker, signal, composite_score, margin_of_safety, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        result.run_id,
                        record.ticker,
                        record.signal,
                        record.composite_score,
                        record.margin_of_safety,
                        record.model_dump_json(),
                    )
                    for record in result.records
                ],
            )

    def record_daily_snapshot(self, result: PipelineResult) -> list[str]:
        """Store today's Strong Value tickers and return names new since the prior day.

        The latest run of a calendar day replaces that day's row. The first
        stored day is a baseline, so it does not mark every name as new.
        """
        day = result.created_at.astimezone(timezone.utc).date().isoformat()
        strong = sorted(record.ticker for record in result.records if record.signal == "Strong Value")
        payload = json.dumps(strong)
        with self._connect() as conn:
            prior = conn.execute(
                """
                SELECT strong_value FROM daily_snapshots
                WHERE snapshot_date < ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (day,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO daily_snapshots (snapshot_date, run_id, strong_value, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    run_id = excluded.run_id,
                    strong_value = excluded.strong_value,
                    created_at = excluded.created_at
                """,
                (day, result.run_id, payload, result.created_at.isoformat()),
            )
        if prior is None:
            logger.info("Stored the first Strong Value daily snapshot (%s names). New entries start tomorrow.", len(strong))
            return []
        previous = set(json.loads(prior["strong_value"]))
        entered = [ticker for ticker in strong if ticker not in previous]
        if entered:
            logger.info("New Strong Value vs the prior daily snapshot: %s", ", ".join(entered))
        return entered

    def latest_run(self) -> PipelineResult | None:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT run_id, created_at, universe, summary FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if run is None:
                return None
            rows = conn.execute(
                "SELECT payload FROM signals WHERE run_id = ? ORDER BY composite_score DESC",
                (run["run_id"],),
            ).fetchall()
        summary = json.loads(run["summary"])
        records = [SignalRecord.model_validate_json(row["payload"]) for row in rows]
        failures = [FetchFailure.model_validate(item) for item in summary.get("failures", [])]
        created = datetime.fromisoformat(run["created_at"])
        return PipelineResult(
            run_id=run["run_id"],
            created_at=created,
            universe=run["universe"],
            records=records,
            failures=failures,
            aaa_yield=float(summary.get("aaa_yield", 0)),
        )

    def save_prices(self, ticker: str, bars: list[tuple[date, float]]) -> None:
        """Store closes. A later bar on the same day replaces the earlier one."""
        key = normalize_ticker(ticker)
        rows = [
            (key, day.isoformat(), float(close))
            for day, close in bars
            if close is not None and close > 0
        ]
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO pit_prices (ticker, price_date, close)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker, price_date) DO UPDATE SET close = excluded.close
                """,
                rows,
            )

    def price_on_or_before(self, ticker: str, day: date) -> tuple[date, float] | None:
        """Latest stored close on or before ``day``."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT price_date, close FROM pit_prices
                WHERE ticker = ? AND price_date <= ?
                ORDER BY price_date DESC
                LIMIT 1
                """,
                (normalize_ticker(ticker), day.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["price_date"]), float(row["close"])

    def fundamentals_as_of(self, day: date) -> list[StockFundamentals]:
        """Latest stored fundamentals for each ticker on or before ``day``.

        A snapshot fetched after ``day`` is not returned.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT as_of_date, ticker, payload FROM pit_fundamentals
                WHERE as_of_date <= ?
                ORDER BY ticker ASC, as_of_date DESC
                """,
                (day.isoformat(),),
            ).fetchall()
        chosen: list[StockFundamentals] = []
        seen: set[str] = set()
        for row in rows:
            ticker = str(row["ticker"])
            if ticker in seen:
                continue
            seen.add(ticker)
            try:
                chosen.append(StockFundamentals.model_validate_json(row["payload"]))
            except Exception:
                logger.exception("Point-in-time fundamentals for %s on %s could not be read", ticker, row["as_of_date"])
        return chosen

    def aaa_yield_as_of(self, day: date) -> float | None:
        """AAA yield stored on the latest scan dated on or before ``day``."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT summary FROM runs
                WHERE substr(created_at, 1, 10) <= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (day.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        summary = json.loads(row["summary"])
        value = summary.get("aaa_yield")
        if value is None:
            return None
        return float(value)

    def pit_date_span(self) -> tuple[date, date] | None:
        """Earliest and latest dates with stored fundamentals."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(as_of_date) AS first_day, MAX(as_of_date) AS last_day FROM pit_fundamentals"
            ).fetchone()
        if row is None or row["first_day"] is None:
            return None
        return date.fromisoformat(row["first_day"]), date.fromisoformat(row["last_day"])

    def _backfill_point_in_time(self) -> None:
        """Copy the current fundamentals cache into the dated tables once.

        The live cache keeps one row per ticker. This preserves that row under
        the day it was fetched so a later refresh does not erase it.
        """
        with self._connect() as conn:
            done = conn.execute("SELECT value FROM meta WHERE key = ?", ("pit_backfill_v1",)).fetchone()
            if done is not None:
                return
            rows = conn.execute("SELECT fetched_at, payload FROM fundamentals").fetchall()
            copied = 0
            for row in rows:
                try:
                    stock = StockFundamentals.model_validate_json(row["payload"])
                except Exception:
                    logger.exception("Skipping a fundamentals row that could not be read during backfill")
                    continue
                _write_pit_row(
                    conn,
                    normalize_ticker(stock.ticker),
                    row["fetched_at"],
                    row["payload"],
                    stock.price,
                )
                copied += 1
            conn.execute(
                """
                INSERT INTO meta (key, value, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    fetched_at = excluded.fetched_at
                """,
                ("pit_backfill_v1", str(copied), utcnow().isoformat()),
            )
        if copied:
            logger.info("Copied %s cached fundamentals rows into the point-in-time tables", copied)

    def previous_alert_tickers(self, exclude_run_id: str) -> set[str]:
        """Tickers that already alerted on the run before this one."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id FROM runs
                WHERE run_id != ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (exclude_run_id,),
            ).fetchone()
            if row is None:
                return set()
            tickers = conn.execute(
                """
                SELECT ticker FROM signals
                WHERE run_id = ?
                  AND signal IN ('Strong Value', 'Moderate Value', 'Deep Value / Net-Net')
                """,
                (row["run_id"],),
            ).fetchall()
        return {str(item["ticker"]) for item in tickers}


def _write_pit_row(
    conn: sqlite3.Connection,
    ticker: str,
    fetched_at: str,
    payload: str,
    price: float | None,
) -> None:
    """Keep one fundamentals snapshot and one close per ticker per calendar day."""
    as_of = _as_of_date(fetched_at)
    conn.execute(
        """
        INSERT INTO pit_fundamentals (as_of_date, ticker, price, payload)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(as_of_date, ticker) DO UPDATE SET
            price = excluded.price,
            payload = excluded.payload
        """,
        (as_of, ticker, price, payload),
    )
    if price is not None and price > 0:
        conn.execute(
            """
            INSERT INTO pit_prices (ticker, price_date, close)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker, price_date) DO UPDATE SET close = excluded.close
            """,
            (ticker, as_of, float(price)),
        )


def _as_of_date(fetched_at: str) -> str:
    parsed = _aware(fetched_at)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
