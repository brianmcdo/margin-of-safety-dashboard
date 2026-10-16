# Margin of Safety

A local research pipeline that screens US equities, estimates intrinsic value four ways, and ranks the names trading at a discount. It is built for patient, absolute-return value work in the spirit of Seth Klarman's *Margin of Safety*: preserve capital, buy at a discount to a conservative value, and hold cash when the discount is not there.

This is not a trading system and it is not investment advice. The output is a ranked reading list. Growth rates, discount rates, and the quality of the vendor data decide the answer.

## What it does

1. Loads a universe (`sample`, S&P 500, a ticker list, or a text file).
2. Fetches price and fundamentals from [Yahoo Finance](https://finance.yahoo.com/) through `yfinance`, with an optional [Financial Modeling Prep](https://site.financialmodelingprep.com/) path when `FMP_API_KEY` is set.
3. Caches each snapshot in SQLite so the next run does not repeat the same calls.
4. Applies configurable screens (P/E, P/B, EV/EBIT, leverage, liquidity, free cash flow).
5. Values each name with:
   - **Graham Number** — `sqrt(22.5 × EPS × book value per share)`
   - **Revised Graham formula** — `EPS × (8.5 + 2g) × 4.4 / Y`, with `Y` from the FRED AAA corporate yield when the network allows
   - **NCAV** — `(current assets − total liabilities) / shares`, plus a stricter haircut on receivables and inventory
   - **Conservative DCF** — median recent free cash flow, a short explicit forecast, and a terminal value that is capped when it would dominate
6. Scores 0–100 from the median margin of safety, cheapness, the balance sheet, and a few quality flags.
7. Labels **Strong Value**, **Moderate Value**, **Watchlist**, or **Deep Value / Net-Net**.
8. Writes a CSV, stores the run, and can print or email names that newly cleared the bar.
9. Opens the same snapshot in a Streamlit dashboard.

Banks and insurers are not treated as industrial net-nets. Energy and mining names get a commodity-price sensitivity grid. That grid scales free cash flow; it is not a reserve NAV.

## Setup

Python 3.11 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional:

```bash
export FMP_API_KEY=your_key          # only if data.source is fmp or auto
export ALERT_EMAIL_FROM=you@example.com
export ALERT_EMAIL_PASSWORD=app_password
```

Email stays off until `alerts.email.enabled` is true in `config.yaml`.

## Run a scan

```bash
python -m src.main --universe sample --min-mos 0.30
python -m src.main --universe INTC,XOM,NEM --limit 3
python -m src.main --universe sp500 --min-mos 0.25
python -m src.main --schedule
```

`--universe` accepts `sample`, `sp500`, a comma-separated list, or a text file with one ticker per line. The first sample run takes a few minutes because each name needs price and three statements. The SQLite cache (`data/value_signals.db`, 20 hours by default) makes the next run local.

The terminal shows the names that clear `--min-mos`. The CSV under `output/` contains every name that was valued, including rejects, so you can see why something was left out.

```bash
pytest
streamlit run src/dashboard/app.py
```

The dashboard reads the latest snapshot. Use the sidebar to run another scan. A limit of `0` means the whole selected universe.

## Docker

```bash
docker compose up --build dashboard
docker compose --profile scan run --rm scan
```

The dashboard is on [http://localhost:8501](http://localhost:8501). The database and CSV files stay in `./data` and `./output`.

A weekday morning cron entry, if you would rather not use `--schedule`:

```cron
0 7 * * 1-5 cd /path/to/value-investing && .venv/bin/python -m src.main --universe sp500 --min-mos 0.25
```

## Configuration

`config.yaml` holds the screens, the 3% DCF growth rate, the 10% discount rate, the 2.5% terminal growth, the 65% terminal-value cap, Graham's growth assumption (5%), and the signal thresholds (40% strong, 25% moderate). Turn any screen off under `screens.enabled`. `screens.deep_value_bypass` lets a stock trading below NCAV skip the earnings and leverage filters. Market-cap and volume floors still apply, because NCAV already subtracts every liability.

`pe_mode: sector_percentile` keeps names in the cheap tail of their sector instead of using a fixed P/E of 15. Sectors with fewer than five observations fall back to the absolute ceiling.

## How a signal is chosen

Margin of safety is `(intrinsic value − price) / intrinsic value`. The headline margin is the median across the methods that returned a number.

- **Strong Value** — passes the screen, median margin at least 40%, and the going-concern methods are not split across the price.
- **Moderate Value** — passes the screen, median margin at least 25%, or the methods disagree at a larger headline discount.
- **Deep Value / Net-Net** — price is below NCAV, the liquidity floors hold, and the earnings methods do not independently support a higher label. Financials are excluded.
- **Watchlist** — the multiples pass, but the discount is still thin.

A name can fail the screen and still be a net-net. It cannot fail the liquidity floors and still be recommended.

Free cash flow from Yahoo is cash from operations minus capital expenditure. That is equity cash flow, so the DCF does not subtract debt again. When at least two annual figures exist, the base cash flow is their median, losses included.

## Tests

```bash
pytest
```

The formula tests are offline and use the closed forms, including Graham's worked shape `EPS × (8.5 + 2g) × 4.4 / Y` and a DCF whose terminal value is forced down to 65% of the total.

## Caveats

- No formula here is a true value. Cheap for a reason is the usual failure, and the screen only catches the obvious cases.
- Yahoo's line items move around. Missing fields fail a filter instead of being treated as a pass.
- The S&P 500 list is the current index, so a backtest of these snapshots would include survivors only.
- Value signals are low frequency. Nothing in this project is meant to be traded on a daily horizon.
- Commodity sensitivity is a cash-flow scale, not a model of reserves, costs, or hedges.
- Do your own work, and treat management, the filings, and the competitive position as mandatory. The machine's job stops at the shortlist.

## Next improvements

- Walk-forward backtest on point-in-time statements, with delisted names included.
- Piotroski F-score and Altman Z as explicit value-trap filters.
- Maintenance capex separated from growth capex before the DCF.
- Sector-relative multiples as the default once a full universe is cached.
- Direct links to the latest 10-K and a short human review checklist per name.
- A real reserve NAV for miners and oil producers, instead of a cash-flow scale.
