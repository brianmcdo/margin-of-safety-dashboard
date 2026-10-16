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
from src.logging_setup import configure_logging
from src.pipeline import run_pipeline
from src.portfolio.builder import build_portfolio
from src.screening.filters import compute_metrics
from src.signals.generator import NONE, records_to_rows
from src.valuation.commodity import (
    assumption_text,
    classify_producer,
    commodity_nav_per_share,
    commodity_sensitivity,
    is_low_cost_producer,
)
from src.valuation.quality import assess_quality

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
        margin-bottom: 0.35rem;
    }
    .formulas {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.65rem 1.4rem;
        background: #fffdf8;
        border: 1px solid #e4dccb;
        border-radius: 8px;
        padding: 0.85rem 1rem 0.95rem;
        margin: 0.15rem 0 1rem;
    }
    .formulas .title {
        grid-column: 1 / -1;
        margin: 0;
        font-size: 1rem;
    }
    .formulas p {
        margin: 0;
        font-size: 0.9rem;
        line-height: 1.4;
    }
    .formulas code {
        color: #1f4e46;
        font-size: 0.86rem;
    }
    .formulas .span {
        grid-column: 1 / -1;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _store(settings):
    return SnapshotStore(str(resolve_path(settings.data.sqlite_path)))


def _render_formulas(settings, aaa_yield: float) -> None:
    """Show the valuation identities actually used for this snapshot."""
    valuation = settings.valuation
    dcf = valuation.dcf
    ncav = valuation.ncav
    weights = settings.scoring.weights
    st.markdown(
        f"""
        <div class="formulas">
          <p class="title"><strong>Formulas</strong></p>
          <p><strong>Margin of safety</strong><br>
          <code>(Intrinsic value − Price) / Intrinsic value</code><br>
          The headline figure is the median across methods that return a value.</p>
          <p><strong>Graham Number</strong><br>
          <code>√(22.5 × EPS × Book value per share)</code><br>
          22.5 is 15 × 1.5. Both inputs must be positive, or the method abstains.</p>
          <p><strong>Graham formula</strong><br>
          <code>V = EPS × (8.5 + 2g) × 4.4 / Y</code><br>
          g is {valuation.graham_growth_percent:g}% growth, capped at
          {valuation.max_graham_growth_percent:g}%. Y is the AAA yield,
          {aaa_yield:.2f}% here. The implied P/E is capped at
          {valuation.max_graham_implied_pe:g}.</p>
          <p><strong>NCAV per share</strong><br>
          <code>(Current assets − Total liabilities) / Shares</code><br>
          Strict version: cash + {ncav.discount_receivables:.0%} of receivables
          + {ncav.discount_inventory:.0%} of inventory + other current assets,
          minus total liabilities.</p>
          <p><strong>Conservative DCF</strong><br>
          <code>Value = PV(explicit cash flows + capped terminal value) / Shares</code><br>
          Median free cash flow over {dcf.normalize_years} years, grown at
          {dcf.growth_rate:.1%} for {dcf.years} years, discounted at
          {dcf.discount_rate:.1%}. Terminal value uses
          {dcf.terminal_growth:.1%} growth and is capped at
          {dcf.max_terminal_share:.0%} of the total.
          The low case uses {dcf.growth_rate - dcf.growth_spread:.1%} growth
          and a {dcf.discount_rate + dcf.discount_spread:.1%} discount. The high
          case uses {dcf.growth_rate + dcf.growth_spread:.1%} growth, discounted
          at {dcf.discount_rate - dcf.discount_spread:.1%}. The reported
          DCF is the base case.</p>
          <p><strong>Quality score</strong><br>
          <code>0–100 from Piotroski F-score, Altman Z, and ROIC or ROE</code><br>
          F-score checks profitability, leverage, and operating efficiency.
          Altman Z below {settings.scoring.quality.altman_distress:g} is a distress flag.
          Returns below {settings.scoring.quality.low_return_threshold:.0%} for two or more years
          are flagged as consistently low. Financials skip Altman Z.</p>
          <p class="span"><strong>Score</strong>
          <code>100 × weighted average of margin of safety, relative cheapness, and quality</code>
          — weights {weights.margin_of_safety:g}, {weights.cheapness:g},
          and {weights.quality:g}. Relative cheapness ranks P/E, P/B, and
          EV/EBIT inside the stock's sector. A sector with fewer than five
          names uses the industry. A quality score below
          {settings.scoring.quality.minimum:.0f} keeps a name off
          Strong Value and Moderate Value.</p>
          <p class="span"><strong>Commodity NAV</strong><br>
          <code>DCF(median free cash flow × planning price / reference price) / Shares</code><br>
          Energy and mining names, matched by sector or industry. Utilities and
          financials are excluded. The planning price is the user's mid-cycle
          price. The reference price is what trailing cash flow is assumed to
          reflect. The sensitivity table reprices that cash flow at several
          multiples of the planning price and at several discount rates.
          This is not a reserve NAV. A low-cost flag needs quality of at least
          {settings.commodity.low_cost_min_quality:.0f},
          positive free cash flow, and debt/equity at or below
          {settings.commodity.low_cost_max_debt_equity:.2f}. The commodity NAV
          stays out of the composite score.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _frame(records) -> pd.DataFrame:
    rows = records_to_rows(records)
    by_ticker = {record.ticker: record for record in records}
    for row in rows:
        record = by_ticker[row["ticker"]]
        row["market_cap"] = record.metrics.get("market_cap")
        row["dcf_low"] = record.dcf_low
        row["dcf_high"] = record.dcf_high
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["margin_of_safety_pct"] = frame["margin_of_safety"] * 100
    return frame


def _quote(settings, name: str) -> float:
    quote = settings.commodity.prices.get(name)
    return 0.0 if quote is None else float(quote.normalized)


def _refresh_producer_flags(frame: pd.DataFrame, settings) -> pd.DataFrame:
    """Recompute the low-cost flag from cached statements for known producers."""
    if frame.empty or "is_commodity_producer" not in frame.columns:
        return frame
    store = _store(settings)
    updated = frame.copy()
    for index, row in updated.loc[updated["is_commodity_producer"].fillna(False)].iterrows():
        stock = store.get_fundamentals(row["ticker"], 24 * 365, allow_expired=True)
        if stock is None:
            continue
        quality = assess_quality(stock, settings.scoring.quality)
        metrics = compute_metrics(stock, settings)
        updated.at[index, "low_cost_producer"] = is_low_cost_producer(
            is_producer=True,
            quality_score=quality.quality_score,
            low_returns=quality.low_returns,
            bankruptcy_risk=quality.bankruptcy_risk,
            free_cash_flow=stock.free_cash_flow,
            debt_to_equity=metrics.debt_to_equity,
            settings=settings.commodity,
        )
    return updated


def _money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"${value:,.2f}"


def _dcf_range(low, base, high) -> str:
    """Show the low-to-high band, or the base case when the band was not stored."""
    if pd.notna(low) and pd.notna(high):
        return f"{_money(float(low))} – {_money(float(high))}"
    if pd.notna(base):
        return _money(float(base))
    return "—"


def _histogram(values: pd.Series, edges: np.ndarray, *, as_percent: bool) -> pd.DataFrame:
    counts, bounds = np.histogram(values.to_numpy(), bins=edges)
    if as_percent:
        labels = [f"{bounds[index]:.0%} to {bounds[index + 1]:.0%}" for index in range(len(counts))]
    else:
        labels = [f"{bounds[index]:.0f}–{bounds[index + 1]:.0f}" for index in range(len(counts))]
    return pd.DataFrame({"Names": counts}, index=labels)


def _sidebar_filters(settings, sectors: list[str], signal_names: list[str], default_signals: list[str]) -> dict:
    with st.sidebar:
        st.page_link("pages/Historical.py", label="Historical screen")
        st.header("Filters")
        st.caption("These apply to the table, the charts, and the CSV. The snapshot itself stays cached.")
        min_mos_pct = st.slider(
            "Minimum margin of safety (%)",
            min_value=0,
            max_value=70,
            value=int(round(settings.valuation.mos.minimum * 100)),
            step=5,
        )
        min_quality = st.slider(
            "Minimum quality score",
            min_value=0,
            max_value=100,
            value=int(round(settings.scoring.quality.minimum)),
            step=5,
        )
        min_cap = st.slider(
            "Minimum market cap ($ billions)",
            min_value=0,
            max_value=200,
            value=0,
            step=10,
        )
        picked_sectors = st.multiselect("Sector", sectors, default=sectors)
        picked_signals = st.multiselect("Signal", signal_names, default=default_signals)
        commodity_only = st.checkbox("Commodity producers only", value=False)
        low_cost_only = st.checkbox("Low-cost producers only", value=False)
        st.caption("Planning prices scale producer cash flow. The reference price stays in config.")
        oil = st.number_input("Oil planning price ($/bbl)", min_value=0.0, value=float(_quote(settings, "oil")), step=5.0)
        gold = st.number_input("Gold planning price ($/oz)", min_value=0.0, value=float(_quote(settings, "gold")), step=50.0)
        copper = st.number_input("Copper planning price ($/lb)", min_value=0.0, value=float(_quote(settings, "copper")), step=0.1, format="%.2f")
        query = st.text_input("Ticker or name", placeholder="Exxon")
    return {
        "min_mos": min_mos_pct / 100,
        "min_quality": float(min_quality),
        "min_market_cap": float(min_cap) * 1_000_000_000,
        "sectors": picked_sectors,
        "signals": picked_signals,
        "commodity_only": commodity_only,
        "low_cost_only": low_cost_only,
        "commodity_prices": {"oil": float(oil), "gold": float(gold), "copper": float(copper)},
        "query": query.strip().lower(),
    }


def _render_scan(settings) -> None:
    with st.sidebar:
        with st.expander("Run a new scan"):
            st.caption("The page loads the last saved snapshot. A scan runs only when you press this button.")
            universe = st.selectbox("Universe", ["sample", "sp500"], index=0)
            custom = st.text_input("Or tickers", placeholder="INTC, XOM, NEM")
            limit = st.number_input("Limit (0 = all)", min_value=0, max_value=500, value=0, step=1)
            refresh = st.checkbox("Ignore the 24-hour cache", value=False)
            run = st.button("Run scan", type="primary")
    if not run:
        return
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
    st.rerun()


def _apply_filters(frame: pd.DataFrame, filters: dict) -> pd.DataFrame:
    view = frame
    if view.empty:
        return view
    if filters["signals"]:
        view = view[view["signal"].isin(filters["signals"])]
    if filters["sectors"]:
        view = view[view["sector"].fillna("Unknown").isin(filters["sectors"])]
    if filters["min_mos"] > 0:
        deep = view["signal"].eq("Deep Value / Net-Net") & view["margin_of_safety"].isna()
        view = view[deep | (view["margin_of_safety"] >= filters["min_mos"])]
    if filters["min_quality"] > 0:
        missing_quality = view["quality_score"].isna()
        view = view[missing_quality | (view["quality_score"] >= filters["min_quality"])]
    if filters["min_market_cap"] > 0:
        view = view[view["market_cap"].fillna(0) >= filters["min_market_cap"]]
    if filters["commodity_only"]:
        view = view[view["is_commodity_producer"]]
    if filters.get("low_cost_only"):
        view = view[view["low_cost_producer"].fillna(False)]
    if filters["query"]:
        needle = filters["query"]
        view = view[
            view["ticker"].str.lower().str.contains(needle, na=False)
            | view["name"].fillna("").str.lower().str.contains(needle, na=False)
        ]
    return view.sort_values(
        ["composite_score", "quality_score", "margin_of_safety"],
        ascending=[False, False, False],
        na_position="last",
    ).reset_index(drop=True)


def _ranked_table(view: pd.DataFrame) -> pd.DataFrame:
    if view.empty:
        return pd.DataFrame(
            columns=["Ticker", "Price", "MoS %", "Graham Number", "DCF range", "Quality", "Signal", "Low-cost", "News", "Score"]
        )
    low_cost = view["low_cost_producer"] if "low_cost_producer" in view.columns else False
    negative = view["negative_news"] if "negative_news" in view.columns else False
    return pd.DataFrame(
        {
            "Ticker": view["ticker"],
            "Price": view["price"],
            "MoS %": view["margin_of_safety"] * 100,
            "Graham Number": view["graham_number"],
            "DCF range": [
                _dcf_range(low, base, high)
                for low, base, high in zip(view["dcf_low"], view["dcf_value"], view["dcf_high"])
            ],
            "Quality": view["quality_score"],
            "Signal": view["signal"],
            "Low-cost": ["Yes" if flag else "" for flag in low_cost],
            "News": ["Negative" if flag else "" for flag in negative],
            "Score": view["composite_score"],
        }
    )


def _render_basket(view: pd.DataFrame, records, settings) -> None:
    """Suggest a basket from the filtered ranking. The snapshot is not changed."""
    st.subheader("Suggested basket")
    st.caption(
        "Takes names in rank order until the next one would break a cap. "
        "Weight that cannot be placed is left unallocated. Not investment advice."
    )
    book = settings.portfolio
    controls = st.columns(3)
    max_sector = controls[0].slider(
        "Max sector weight (%)",
        min_value=5,
        max_value=100,
        value=int(round(book.max_sector_weight * 100)),
        step=5,
        key="basket_sector",
    )
    max_position = controls[1].slider(
        "Max position size (%)",
        min_value=1,
        max_value=100,
        value=int(round(book.max_position_size * 100)),
        step=1,
        key="basket_position",
    )
    min_holdings = controls[2].number_input(
        "Minimum holdings",
        min_value=1,
        max_value=40,
        value=int(book.min_holdings),
        step=1,
        key="basket_holdings",
    )
    by_ticker = {record.ticker: record for record in records}
    ordered = [by_ticker[ticker] for ticker in view["ticker"].tolist() if ticker in by_ticker]
    try:
        basket = build_portfolio(
            ordered,
            max_sector_weight=max_sector / 100,
            max_position_size=max_position / 100,
            min_holdings=int(min_holdings),
        )
    except ValueError as exc:
        st.warning(str(exc))
        return
    if not basket.holdings:
        st.info("No names fit these caps.")
        for note in basket.notes:
            st.caption(note)
        return
    rows = pd.DataFrame(
        {
            "Ticker": [holding.ticker for holding in basket.holdings],
            "Name": [holding.name or "" for holding in basket.holdings],
            "Sector": [holding.sector for holding in basket.holdings],
            "Signal": [holding.signal for holding in basket.holdings],
            "Score": [holding.composite_score for holding in basket.holdings],
            "Weight %": [holding.weight * 100 for holding in basket.holdings],
        }
    )
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%.1f"),
            "Weight %": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    sectors = basket.sector_weights()
    mix = " · ".join(f"{name} {weight:.0%}" for name, weight in sorted(sectors.items()))
    st.caption(f"Invested {basket.invested:.0%}. {mix}")
    for note in basket.notes:
        st.warning(note)
    st.download_button(
        "Download basket CSV",
        rows.to_csv(index=False).encode("utf-8"),
        file_name="suggested_basket.csv",
        mime="text/csv",
    )


def _render_charts(view: pd.DataFrame) -> None:
    st.subheader("Distributions")
    left, right = st.columns(2)
    with left:
        st.markdown("**Margin of safety**")
        if view.empty or not view["margin_of_safety"].notna().any():
            st.caption("No margin of safety values in this filter.")
        else:
            edges = np.array([-1.5, -1.0, -0.5, -0.25, 0.0, 0.15, 0.25, 0.40, 0.60, 1.0])
            st.bar_chart(
                _histogram(view["margin_of_safety"].dropna(), edges, as_percent=True),
                height=240,
            )
    with right:
        st.markdown("**Composite score**")
        if view.empty or not view["composite_score"].notna().any():
            st.caption("No scores in this filter.")
        else:
            st.bar_chart(
                _histogram(view["composite_score"].dropna(), np.linspace(0, 100, 11), as_percent=False),
                height=240,
            )


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

    st.title("Margin of Safety")
    st.markdown(
        '<p class="disclaimer">Equity research signals from Graham, NCAV, and a conservative '
        "free-cash-flow model. A high score is a candidate for reading, not an instruction "
        "to buy. Not investment advice.</p>",
        unsafe_allow_html=True,
    )

    result = _store(settings).latest_run()
    aaa_yield = result.aaa_yield if result is not None else settings.fred.fallback_aaa_yield
    _render_formulas(settings, aaa_yield)
    if result is None or not result.records:
        _render_scan(settings)
        st.info("No snapshot yet. Run a sample scan from the sidebar. The first pass needs the network.")
        return

    frame = _refresh_producer_flags(_frame(result.records), settings)
    sectors = sorted(frame["sector"].fillna("Unknown").unique())
    signal_names = sorted(frame["signal"].dropna().unique())
    actionable = [name for name in signal_names if name not in {NONE, "No Signal"}]
    filters = _sidebar_filters(settings, sectors, signal_names, actionable or signal_names)
    _render_scan(settings)

    view = _apply_filters(frame, filters)
    counts = pd.Series([record.signal for record in result.records]).value_counts()

    st.caption(
        f"Loaded from the saved snapshot {result.run_id} · universe {result.universe} · "
        f"AAA yield {result.aaa_yield:.2f}% · {len(result.failures)} fetch failures. "
        "Opening this page does not request new market data."
    )
    metrics = st.columns(5)
    short_labels = [
        ("Strong Value", "Strong"),
        ("Moderate Value", "Moderate"),
        ("Deep Value / Net-Net", "Net-net"),
        ("Watchlist", "Watchlist"),
        ("Avoid", "Avoid"),
    ]
    for column, (label, short) in zip(metrics, short_labels, strict=False):
        if label == "Avoid":
            value = int(counts.get("Avoid", 0) + counts.get("No Signal", 0))
        else:
            value = int(counts.get(label, 0))
        column.metric(short, value)

    _render_charts(view)
    shown = _ranked_table(view)
    header = st.columns([3, 1])
    header[0].subheader(f"Ranked names  ·  {len(shown)}")
    header[1].download_button(
        "Download CSV",
        shown.to_csv(index=False).encode("utf-8"),
        file_name=f"signals_{result.run_id}.csv",
        mime="text/csv",
        width="stretch",
    )
    if shown.empty:
        st.warning("Nothing matches these filters.")
        return

    _render_basket(view, result.records, settings)
    tickers = shown["Ticker"].tolist()
    if st.session_state.get("stock") not in tickers:
        st.session_state["stock"] = tickers[0]

    def _sync_row() -> None:
        rows = st.session_state["ranked_table"].selection.rows
        if rows:
            st.session_state["stock"] = tickers[rows[0]]

    st.dataframe(
        shown,
        width="stretch",
        hide_index=True,
        on_select=_sync_row,
        selection_mode="single-row",
        key="ranked_table",
        column_config={
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "MoS %": st.column_config.NumberColumn(format="%.1f"),
            "Graham Number": st.column_config.NumberColumn(format="$%.2f"),
            "Quality": st.column_config.NumberColumn(format="%.0f"),
            "Score": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    selected = st.selectbox("Select a stock", tickers, key="stock")
    record = next(item for item in result.records if item.ticker == selected)
    st.subheader(f"{record.ticker}  ·  {record.name or record.ticker}")
    st.caption(f"{record.sector or 'Unknown sector'}  ·  {record.industry or 'Industry not reported'}")
    flagged = frame.loc[frame["ticker"] == record.ticker, "low_cost_producer"]
    if not flagged.empty and bool(flagged.iloc[0]):
        st.success(
            "Low-cost producer. Quality, positive free cash flow, and modest debt clear the configured floors. "
            "This is not an all-in sustaining cost."
        )
    detail = st.columns(4)
    detail[0].metric("Price", f"${record.price:,.2f}" if record.price is not None else "—")
    detail[1].metric(
        "Median margin of safety",
        "—" if record.margin_of_safety is None else f"{record.margin_of_safety:.1%}",
    )
    detail[2].metric("Score", f"{record.composite_score:.1f}")
    detail[3].metric("Signal", record.signal)
    quality_bits = st.columns(4)
    quality_bits[0].metric(
        "Quality",
        "—" if record.quality_score is None else f"{record.quality_score:.0f}",
    )
    quality_bits[1].metric(
        "F-score",
        "—" if record.f_score_available <= 0 else f"{record.f_score}/{record.f_score_available}",
    )
    quality_bits[2].metric("Altman Z", "—" if record.altman_z is None else f"{record.altman_z:.2f}")
    if record.roic is not None:
        return_label, return_value = "ROIC", record.roic
    elif record.roe is not None:
        return_label, return_value = "ROE", record.roe
    else:
        return_label, return_value = "ROIC", None
    quality_bits[3].metric(
        return_label,
        "—" if return_value is None else f"{return_value:.1%}",
    )
    if record.bankruptcy_risk == "distress" or record.low_returns:
        flags = []
        if record.bankruptcy_risk == "distress":
            flags.append("bankruptcy risk is in the distress zone")
        if record.low_returns:
            flags.append("returns have been consistently low")
        st.warning("Quality flag: " + "; ".join(flags) + ".")
    if record.negative_news:
        titles = "; ".join(f'"{title}"' for title in record.news_headlines[:4])
        st.warning(
            "Very negative recent headlines"
            + (f": {titles}" if titles else "")
            + ". This is a keyword check on Yahoo titles, not a filing review."
        )
    st.markdown("**Why it was flagged**")
    if record.reasons:
        for reason in record.reasons:
            st.markdown(f"- {reason}")
    else:
        st.caption("This snapshot has no stored reasons for this name.")
    if record.screen_failures:
        st.warning("Screen: " + "; ".join(record.screen_failures))

    st.markdown("**Valuation methods**")

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
    if record.dcf_low is not None or record.dcf_high is not None:
        low = "—" if record.dcf_low is None else f"${record.dcf_low:,.2f}"
        base = "—" if record.dcf_value is None else f"${record.dcf_value:,.2f}"
        high = "—" if record.dcf_high is None else f"${record.dcf_high:,.2f}"
        st.caption(f"DCF range: low {low} · base {base} · high {high}")
    elif record.dcf_value is not None:
        st.caption(f"DCF base case ${record.dcf_value:,.2f}. This snapshot has no low/high band.")
    comparable = [
        ("1 Price", record.price),
        ("2 Graham Number", record.graham_number),
        ("3 Graham Formula", record.graham_formula),
        ("4 DCF", record.dcf_value),
        ("5 NCAV", record.ncav_per_share),
        ("6 Strict NCAV", record.ncav_strict_per_share),
    ]
    chart = pd.DataFrame(
        {"Per share": [value if value is not None and value > 0 else np.nan for _label, value in comparable]},
        index=[label for label, _value in comparable],
    )
    st.bar_chart(chart, height=260)

    st.markdown("**Commodity sensitivity**")
    prices = filters["commodity_prices"]
    stock = _store(settings).get_fundamentals(record.ticker, 24 * 365, allow_expired=True)
    profile = classify_producer(stock, settings.commodity) if stock is not None else None
    cells = record.sensitivity
    nav = record.commodity_nav
    nav_note = ""
    if stock is not None and profile is not None:
        nav, nav_note = commodity_nav_per_share(
            stock,
            settings.commodity,
            settings.valuation.dcf,
            normalized_overrides=prices,
        )
        cells = commodity_sensitivity(
            stock,
            settings.commodity,
            settings.valuation.dcf,
            normalized_overrides=prices,
        )
    if profile is not None or record.is_commodity_producer:
        if nav is not None:
            st.metric("Commodity NAV / share", f"${nav:,.2f}")
        st.caption(assumption_text(settings.commodity))
        if nav_note:
            st.caption(nav_note)
    if cells:
        grid = pd.DataFrame(
            [
                {
                    "Commodity price": (
                        cell.price_multiplier
                        if cell.commodity_price is None
                        else cell.commodity_price
                    ),
                    "Discount rate": cell.discount_rate,
                    "Value": None if cell.value_per_share is None else round(cell.value_per_share, 2),
                }
                for cell in cells
            ]
        )
        pivot = grid.pivot(index="Commodity price", columns="Discount rate", values="Value")
        pivot = pivot.sort_index().sort_index(axis=1)
        if cells and cells[0].commodity_price is None:
            pivot.index = [f"{value:.0%} of base cash flow" for value in pivot.index]
        else:
            unit = profile.unit if profile is not None else (record.commodity_unit or "")
            pivot.index = [f"{value:,.2f} {unit}".rstrip() for value in pivot.index]
        pivot.columns = [f"{value:.0%} discount" for value in pivot.columns]
        st.dataframe(pivot, width="stretch")
    else:
        st.caption("No commodity-price sensitivity table for this name. That grid is built for energy and mining companies.")


if __name__ == "__main__":
    main()
else:
    main()
