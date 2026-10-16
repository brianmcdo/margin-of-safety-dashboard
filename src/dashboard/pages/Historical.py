"""Historical screen: replay a stored snapshot and show forward price returns."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from src.backtest.engine import ONE_YEAR, add_months, run_historical_screen
from src.backtest.history import fetch_closes
from src.config import load_settings, resolve_path
from src.data.cache import SnapshotStore
from src.logging_setup import configure_logging

st.set_page_config(page_title="Historical screen", page_icon="◎", layout="wide")


def main() -> None:
    try:
        settings = load_settings()
    except (OSError, ValueError) as exc:
        st.error(f"Could not load config.yaml: {exc}")
        return
    configure_logging(
        console_level=settings.logging.level,
        file_level=settings.logging.file_level,
        log_file=settings.logging.file,
    )
    st.title("Historical screen")
    st.markdown(
        "Replay the screener on fundamentals and closes stored on or before a date. "
        "The result is the equal-weight price return of the names that cleared the filters, "
        "next to the equal-weight return of every name valued that day. "
        "Dividends, costs, and delistings are left out. This is not a portfolio and not investment advice."
    )
    store = SnapshotStore(str(resolve_path(settings.data.sqlite_path)))
    span = store.pit_date_span()
    if span is None:
        st.info("No point-in-time snapshot is stored yet. Run a scan first. Each scan keeps the day it was fetched.")
        return
    first_day, last_day = span
    st.caption(f"Stored snapshots run from {first_day.isoformat()} to {last_day.isoformat()}.")

    with st.sidebar:
        as_of = st.date_input(
            "As of",
            value=last_day,
            min_value=first_day,
            max_value=max(last_day, date.today()),
        )
        min_mos = st.slider("Minimum margin of safety (%)", min_value=0, max_value=70, value=int(round(settings.valuation.mos.minimum * 100)), step=5)
        min_quality = st.slider("Minimum quality score", min_value=0, max_value=100, value=int(round(settings.scoring.quality.minimum)), step=5)
        signal = st.selectbox(
            "Signal",
            ["Default (strong, moderate, deep)", "strong", "moderate", "deep", "watchlist", "avoid", "all"],
        )
        benchmark = st.text_input("Benchmark ticker", value=settings.backtest.benchmark)
        fetch = st.checkbox("Download missing closes from Yahoo", value=False)
        st.caption("The download fills prices through today. It does not invent a close after the last trading day.")
        run = st.button("Run historical screen", type="primary")

    if not run:
        st.caption("Choose a date and run the screen. Opening this page does not fetch market data.")
        return

    signal_arg = None if signal.startswith("Default") else signal
    if fetch:
        tickers = [stock.ticker for stock in store.fundamentals_as_of(as_of)]
        tickers.append(benchmark.strip() or "SPY")
        end = min(
            date.today(),
            add_months(as_of, ONE_YEAR) + timedelta(days=settings.backtest.price_tolerance_days),
        )
        with st.spinner("Downloading closes…"):
            bars = fetch_closes(tickers, as_of - timedelta(days=7), end)
        for ticker, series in bars.items():
            store.save_prices(ticker, series)
        st.caption(f"Stored closes for {len(bars)} names.")

    with st.spinner("Valuing the stored snapshot…"):
        result = run_historical_screen(
            store,
            settings,
            as_of,
            min_mos=min_mos / 100,
            min_quality=float(min_quality),
            signal=signal_arg,
            benchmark=benchmark.strip() or "SPY",
        )
    summary = result.summary
    counts = st.columns(4)
    counts[0].metric("Valued", summary.universe_size)
    counts[1].metric("Selected", summary.selected_count)
    counts[2].metric("Selected 6-month", _fmt(summary.selected_6m))
    counts[3].metric("Selected 1-year", _fmt(summary.selected_1y))
    compare = st.columns(4)
    compare[0].metric("Universe 6-month", _fmt(summary.universe_6m))
    compare[1].metric("Excess 6-month", _fmt(summary.excess_6m))
    compare[2].metric("Universe 1-year", _fmt(summary.universe_1y))
    compare[3].metric("Excess 1-year", _fmt(summary.excess_1y))
    bench = st.columns(2)
    bench[0].metric(f"{summary.benchmark_ticker} 6-month", _fmt(summary.benchmark_6m))
    bench[1].metric(f"{summary.benchmark_ticker} 1-year", _fmt(summary.benchmark_1y))
    for note in summary.notes:
        st.caption(note)

    frame = pd.DataFrame(
        [
            {
                "Ticker": row.ticker,
                "Name": row.name,
                "Signal": row.signal,
                "Score": row.composite_score,
                "MoS %": None if row.margin_of_safety is None else row.margin_of_safety * 100,
                "Entry": row.entry_price,
                "6-month %": "—" if row.return_6m is None else f"{row.return_6m:.1%}",
                "1-year %": "—" if row.return_1y is None else f"{row.return_1y:.1%}",
            }
            for row in result.selected
        ]
    )
    if frame.empty:
        st.info("No names cleared these filters on the stored snapshots.")
        return
    st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "MoS %": st.column_config.NumberColumn(format="%.1f"),
            "Entry": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    st.download_button(
        "Download CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name=f"backtest_{as_of.isoformat()}.csv",
        mime="text/csv",
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


if __name__ == "__main__":
    main()
else:
    main()
