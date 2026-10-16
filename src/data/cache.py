"""SQLite cache for fundamentals, universe lists, and signal runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.models import FetchFailure, PipelineResult, SignalRecord, StockFundamentals, utcnow


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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get_fundamentals(self, ticker: str, ttl_hours: float) -> StockFundamentals | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM fundamentals WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if utcnow() - fetched_at > timedelta(hours=ttl_hours):
            return None
        try:
            return StockFundamentals.model_validate_json(row["payload"])
        except Exception:
            return None

    def save_fundamentals(self, stock: StockFundamentals) -> None:
        payload = stock.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fundamentals (ticker, fetched_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    payload = excluded.payload
                """,
                (stock.ticker.upper(), stock.fetched_at.isoformat(), payload),
            )

    def get_ticker_list(self, name: str, ttl_hours: float) -> list[str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM ticker_lists WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
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
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
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
