"""Streamlit research dashboard for the latest value-signal snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import streamlit as st

from src.config import load_settings, resolve_path
from src.data.cache import SnapshotStore
from src.pipeline import run_pipeline
from src.signals.generator import NONE, rank_records, records_to_rows

st.set_page_config(
    page_title="Margin of Safety",
    page_icon="◎",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp { background: #f4f0e6; color: #1c1915; }
    h1, h2, h3 { letter-spacing: -0.03em; font-weight: 600; }
    div[data-testid="stMetric"] {
        background: #fffdf8;
        border: 1px solid #e4dccb;
        padding: 0.6rem 0.8rem;
        border-radius: 8px;
    }
    .disclaimer {
        color: #5c564c;
        font-size: 0.92rem;
        margin-bottom: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _store(settings):
    return SnapshotStore(str(resolve_path(settings.data.sqlite_path)))


def _frame(records) -> pd.DataFrame:
    frame = pd.DataFrame(records_to_rows(records))
    if frame.empty:
        return frame
    frame["margin_of_safety_pct"] = frame["margin_of_safety"] * 100
    return frame


def main() -> None:
    try:
        settings = load_settings()
    except (OSError, ValueError) as exc:
        st.error(f"Could not load config.yaml: {exc}")
        return

    st.title("Margin of Safety")
    st.markdown(
        '<p class="disclaimer">Equity research signals from Graham, NCAV, and a conservative '
        "free-cash-flow model. A high score is a candidate for reading, not an instruction "
        "to buy. Not investment advice.</p>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Scan")
        universe = st.selectbox("Universe", ["sample", "sp500"], index=0)
        custom = st.text_input("Or tickers", placeholder="INTC, XOM, NEM")
        limit = st.number_input("Limit (0 = all)", min_value=0, max_value=500, value=0, step=1)
        refresh = st.checkbox("Refresh cache", value=False)
        min_mos_pct = st.slider(
            "Minimum margin of safety (%)",
            min_value=0,
            max_value=70,
            value=int(round(settings.valuation.mos.minimum * 100)),
            step=5,
        )
        min_mos = min_mos_pct / 100
        st.caption(f"Ranked count keeps names at or above {min_mos_pct}% margin of safety.")
        run = st.button("Run scan", type="primary")

    if run:
        chosen = custom.strip() if custom.strip() else universe
        progress = st.progress(0.0, text="Starting")

        def _on_progress(done: int, total: int, ticker: str) -> None:
            fraction = 0.0 if total <= 0 else done / total
            progress.progress(min(fraction, 1.0), text=f"{done}/{total}  {ticker}")

        with st.spinner("Fetching fundamentals and valuing the universe…"):
            try:
                run_pipeline(
                    settings,
                    universe=chosen,
                    refresh=refresh,
                    limit=None if limit == 0 else int(limit),
                    progress=_on_progress,
                )
            except Exception as exc:
                st.error(f"Scan failed: {exc}")
                return
        progress.progress(1.0, text="Scan stored")

    result = _store(settings).latest_run()
    if result is None or not result.records:
        st.info("No snapshot yet. Run a sample scan from the sidebar. The first pass needs the network.")
        return

    ranked = rank_records(result.records, min_mos)
    frame = _frame(result.records)
    counts = pd.Series([record.signal for record in result.records]).value_counts()

    st.caption(
        f"Run {result.run_id} · universe {result.universe} · "
        f"AAA yield {result.aaa_yield:.2f}% · {len(result.failures)} fetch failures"
    )
    metrics = st.columns(5)
    short_labels = [
        ("Strong Value", "Strong"),
        ("Moderate Value", "Moderate"),
        ("Deep Value / Net-Net", "Net-net"),
        ("Watchlist", "Watchlist"),
        ("No Signal", "No signal"),
    ]
    for column, (label, short) in zip(metrics, short_labels, strict=False):
        column.metric(short, int(counts.get(label, 0)))

    sectors = sorted({(record.sector or "Unknown") for record in result.records})
    signal_names = sorted({record.signal for record in result.records})
    actionable = [name for name in signal_names if name != NONE]
    filter_cols = st.columns([1.4, 1.4, 1])
    with filter_cols[0]:
        picked_signals = st.multiselect("Signal", signal_names, default=actionable or signal_names)
    with filter_cols[1]:
        picked_sectors = st.multiselect("Sector", sectors, default=sectors)
    with filter_cols[2]:
        commodity_only = st.checkbox("Commodity only", value=False)
    query = st.text_input("Find ticker or name", placeholder="Exxon")

    view = frame
    if not view.empty:
        if picked_signals:
            view = view[view["signal"].isin(picked_signals)]
        if picked_sectors:
            view = view[view["sector"].fillna("Unknown").isin(picked_sectors)]
        if commodity_only:
            view = view[view["is_commodity_producer"]]
        if query.strip():
            needle = query.strip().lower()
            view = view[
                view["ticker"].str.lower().str.contains(needle, na=False)
                | view["name"].fillna("").str.lower().str.contains(needle, na=False)
            ]
        view = view.sort_values(
            ["composite_score", "margin_of_safety"],
            ascending=[False, False],
            na_position="last",
        )
        view["mos_pct"] = view["margin_of_safety"] * 100

    st.subheader(f"Candidates  ·  {len(ranked)} clear {min_mos:.0%} margin of safety")
    display_columns = [
        "ticker",
        "name",
        "signal",
        "composite_score",
        "mos_pct",
        "price",
        "graham_number",
        "graham_formula",
        "dcf_value",
        "ncav_per_share",
        "pe",
        "pb",
        "ev_ebit",
        "debt_to_equity",
        "sector",
    ]
    if view.empty:
        st.warning("Nothing matches these filters.")
    else:
        shown = view[[column for column in display_columns if column in view.columns]]
        st.dataframe(
            shown,
            width="stretch",
            hide_index=True,
            column_config={
                "mos_pct": st.column_config.NumberColumn("MoS %", format="%.1f"),
                "composite_score": st.column_config.NumberColumn("Score", format="%.1f"),
                "price": st.column_config.NumberColumn("Price", format="$%.2f"),
                "graham_number": st.column_config.NumberColumn("Graham", format="$%.2f"),
                "graham_formula": st.column_config.NumberColumn("Formula", format="$%.2f"),
                "dcf_value": st.column_config.NumberColumn("DCF", format="$%.2f"),
                "ncav_per_share": st.column_config.NumberColumn("NCAV", format="$%.2f"),
                "pe": st.column_config.NumberColumn("P/E", format="%.1f"),
                "pb": st.column_config.NumberColumn("P/B", format="%.2f"),
                "ev_ebit": st.column_config.NumberColumn("EV/EBIT", format="%.1f"),
                "debt_to_equity": st.column_config.NumberColumn("D/E", format="%.2f"),
            },
        )

    chart_source = view if not view.empty else frame
    if not chart_source.empty and chart_source["margin_of_safety"].notna().any():
        st.subheader("Margin of safety distribution")
        bins = np.array([-1.5, -1.0, -0.5, -0.25, 0.0, 0.15, 0.25, 0.40, 0.60, 1.0])
        values = chart_source["margin_of_safety"].dropna().to_numpy()
        counts_hist, edges = np.histogram(values, bins=bins)
        labels_hist = [
            f"{index + 1}. {edges[index]:.0%} to {edges[index + 1]:.0%}"
            for index in range(len(counts_hist))
        ]
        st.bar_chart(pd.DataFrame({"Names": counts_hist}, index=labels_hist), height=240)

    st.subheader("Valuation breakdown")
    choices = view["ticker"].tolist() if not view.empty else []
    if not choices:
        st.caption("Select a broader filter to inspect a name.")
        return
    selected = st.selectbox("Ticker", choices)
    record = next(item for item in result.records if item.ticker == selected)
    detail = st.columns(4)
    detail[0].metric("Price", f"${record.price:,.2f}" if record.price is not None else "—")
    detail[1].metric(
        "Median margin of safety",
        "—" if record.margin_of_safety is None else f"{record.margin_of_safety:.1%}",
    )
    detail[2].metric("Score", f"{record.composite_score:.1f}")
    detail[3].metric("Signal", record.signal)
    st.write(f"**{record.name or record.ticker}**  ·  {record.sector or 'Unknown sector'}  ·  {record.industry or ''}")
    for reason in record.reasons:
        st.markdown(f"- {reason}")
    if record.screen_failures:
        st.warning("Screen: " + "; ".join(record.screen_failures))

    method_rows = [
        {
            "Method": item.method,
            "Value / share": item.intrinsic_value_per_share,
            "Margin of safety": (
                None if item.margin_of_safety is None else f"{item.margin_of_safety:.1%}"
            ),
            "Note": item.note,
        }
        for item in record.methods
    ]
    st.dataframe(
        pd.DataFrame(method_rows),
        width="stretch",
        hide_index=True,
        column_config={
            "Value / share": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    comparable = [
        ("1 Price", record.price),
        ("2 Graham Number", record.graham_number),
        ("3 Graham Formula", record.graham_formula),
        ("4 DCF", record.dcf_value),
        ("5 NCAV", record.ncav_per_share),
    ]
    chart = pd.DataFrame(
        {"Per share": [value if value is not None and value > 0 else np.nan for _label, value in comparable]},
        index=[label for label, _value in comparable],
    )
    st.bar_chart(chart, height=260)

    if record.sensitivity:
        st.subheader("Commodity sensitivity")
        st.caption(
            "Per-share equity value if normalized free cash flow is scaled by a commodity-price "
            "factor and discounted at an alternate rate. 1.00 is the base cash flow."
        )
        grid = pd.DataFrame(
            [
                {
                    "Price factor": cell.price_multiplier,
                    "Discount rate": cell.discount_rate,
                    "Value": None if cell.value_per_share is None else round(cell.value_per_share, 2),
                }
                for cell in record.sensitivity
            ]
        )
        pivot = grid.pivot(index="Price factor", columns="Discount rate", values="Value")
        pivot = pivot.sort_index().sort_index(axis=1)
        pivot.index = [f"{value:.0%}" for value in pivot.index]
        pivot.columns = [f"{value:.0%}" for value in pivot.columns]
        st.dataframe(pivot, width="stretch")


if __name__ == "__main__":
    main()
else:
    main()
