# Margin of Safety

A local research pipeline that screens US equities, estimates intrinsic value four ways, and ranks the names trading at a discount. It is built for patient value work in the spirit of Seth Klarman's *Margin of Safety*: preserve capital, buy at a discount to a conservative value, and hold cash when the discount is not there.

This is not a trading system and it is not investment advice. The output is a ranked reading list. The discount rate, the growth rate, and the quality of the vendor data decide the answer. The defaults are deliberately conservative. Raising the growth rate or cutting the discount rate will make more names look cheap, and that result is only as careful as the assumptions you typed in.

Cheapness is judged against the stock's sector, or its industry when the sector has fewer than five names. A scan also stores recent Yahoo headlines and flags titles that are severely negative. The ranked list can be turned into a suggested basket with a sector cap, a position cap, and a minimum number of holdings. Those three pieces are written up in [docs/scoring.md](docs/scoring.md), [docs/news.md](docs/news.md), and [docs/portfolio.md](docs/portfolio.md). A saved snapshot keeps the scores and headlines from the scan that wrote it. Run a new scan after changing the scoring or news settings.

## One command

Docker starts the dashboard. The database, logs, and CSV files stay on your machine.

```bash
docker compose up --build
```

Open [http://localhost:8501](http://localhost:8501). The page reads the latest saved snapshot. The first time, that snapshot does not exist yet, so run a scan in another terminal:

```bash
docker compose --profile scan run --rm scan
docker compose --profile scan run --rm scan --universe INTC,XOM,NEM --limit 3
```

The scan uses `config.yaml` for the universe, the discount rate, the growth rate, and the thresholds. Edit that file and run the scan again. You do not rebuild the image to change those settings, because the file is mounted into the container.

`FMP_API_KEY` in the environment is passed through when you want Financial Modeling Prep as a fallback. Leave it unset to use Yahoo Finance.

## Setup without Docker

Python 3.11 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional email alerts. Email stays off until `alerts.email.enabled` is true in `config.yaml`.

```bash
export ALERT_EMAIL_FROM=you@example.com
export ALERT_EMAIL_PASSWORD=app_password
```

## Example commands

```bash
python -m src.main --universe sample
python -m src.main --universe INTC,XOM,NEM --limit 3
python -m src.main --universe sp500 --min-mos 0.30 --min-quality 40
python -m src.main --universe sample --min-mos 0.30 --min-quality 50 --signal strong
python -m src.main --as-of 2024-01-15
python -m src.main --schedule
streamlit run src/dashboard/app.py
pytest
```

`--universe` accepts `sample`, `sp500`, a comma-separated list, or a text file with one ticker per line. The sample names live under `universe.sample` in `config.yaml`. `--min-mos 0.30` keeps Strong and Moderate names whose median margin of safety is at least 30%. `--min-quality 50` requires a quality score of at least 50 for those two labels. `--signal` is `strong`, `moderate`, `deep`, `watchlist`, `avoid`, or `all`. The default table is Strong Value, Moderate Value, and Deep Value / Net-Net.

The first sample run takes a few minutes because each name needs a price and three statements. The SQLite cache (`data/value_signals.db`) stores each ticker with the time it was fetched. A ticker newer than 24 hours (`universe.cache_ttl_hours`) is not requested again unless you pass `--refresh`. If a live fetch still fails, the last snapshot is used and the failure is logged. One bad ticker does not stop the rest of the universe.

The terminal shows the names that clear the filters, then a suggested basket. The table includes a News column: `Neg` when recent headlines were flagged. The CSV under `output/` contains every name that was valued, including rejects.

The dashboard loads that saved snapshot. The sidebar filters the table, the charts, and the CSV. Oil, gold, and copper planning prices reprice the commodity table from the cached statements. They do not start a new fetch. The Historical page replays a stored snapshot. A limit of `0` on the scan form means the whole selected universe.

## Example output

This is the offline fixture in `tests/fixtures.py`, valued with the default assumptions and a 6% AAA yield. It is not a live quote. `VAL` is a solvent industrial at $40 with $5 of earnings, $50 of book value, and positive free cash flow. `LOSS` has negative earnings. `DEBT` has the same earnings power as `VAL` and a debt-to-equity ratio of 8.

```text
Ranked value signals (strong, moderate, deep; margin of safety ≥ 30%; quality ≥ 40)

Ticker  Signal        Score  Quality  MoS    Price  Graham   DCF
VAL     Strong Value  77.7   74       42.1%  40.00  75.00    70.48

Valued 3 names. Avoid 2, Strong Value 1. Ranked 1. AAA yield 6.00%.
Wrote output/signals_<run>.csv

VAL was flagged: Graham Number 47% discount + positive FCF + low debt + quality 74
```

What the columns mean:

| Column | VAL | How to read it |
| --- | --- | --- |
| Graham Number | $75.00 | `sqrt(22.5 × 5 × 50)`. Price is $40, so this method alone is a 47% discount. |
| DCF | $70.48 | Median free cash flow, grown at 3% and discounted at 10%. |
| NCAV | $10.00 | Current assets minus all liabilities, per share. Price is above it, so this is not a net-net. |
| Margin of safety | 42.1% | Median of the methods that returned a value. |
| Score | 77.7 | Margin of safety, sector-relative cheapness, and quality. 100 is the top of the scale. |
| Signal | Strong Value | The screen passed, the discount clears 40%, and the quality score clears 40. |

`LOSS` is Avoid because the earnings methods abstain and the discount is not there. `DEBT` still shows a large discount to the Graham Number, and it is still Avoid, because debt/equity of 8 fails the screen. A cheap balance sheet that cannot survive is not a Strong Value name.

## Valuation methods

Margin of safety is `(intrinsic value − price) / intrinsic value`. The headline figure is the median of the methods that return a number. A method that lacks data returns nothing and a short reason. It does not crash the scan.

### Graham Number

```text
sqrt(22.5 × EPS × book value per share)
```

22.5 is Graham's defensive ceiling of 15 times earnings combined with 1.5 times book. Both inputs must be positive. A loss, a negative book value, or zero shares makes the method abstain. Those constants are the definition of the method. They are not settings.

### Revised Graham formula

```text
V = EPS × (8.5 + 2g) × 4.4 / Y
```

`g` is expected growth in whole-number percent (`valuation.graham_growth_percent`, default 5). It is capped at 15%. `Y` is the Moody's AAA corporate yield from FRED series `DAAA`, in percent. If FRED does not answer, the scan uses `fred.fallback_aaa_yield` (6.0 unless you change it). 8.5 is Graham's no-growth price/earnings ratio. 4.4 was the AAA yield when he published the formula, so a higher bond yield shrinks the value. The implied price/earnings ratio is capped (`valuation.max_graham_implied_pe`, default 40) so a very low bond yield cannot produce an enormous value. Negative earnings make the method abstain.

### NCAV

```text
(current assets − total liabilities) / shares
```

The strict version haircuts the current assets before subtracting liabilities: cash in full, 75% of receivables, 50% of inventory, and the remaining other current assets. Those haircuts are `valuation.ncav.discount_receivables` and `valuation.ncav.discount_inventory`. Banks and insurers are not treated as net-nets. A price below NCAV can still be Deep Value / Net-Net when the liquidity floors hold, even if the earnings screen fails.

### Conservative DCF

```text
value = present value of explicit cash flows + capped terminal value, per share
```

Free cash flow is cash from operations minus capital expenditure. That is equity cash flow, so the model does not subtract debt again. The base is the median of the recent annual figures (`valuation.dcf.normalize_years`, default 3), losses included. It is grown at `valuation.dcf.growth_rate` (default 3%) for `valuation.dcf.years` (default 8), discounted at `valuation.dcf.discount_rate` (default 10%). The terminal value uses `valuation.dcf.terminal_growth` (default 2.5%) and is scaled down when it would be more than `valuation.dcf.max_terminal_share` (default 65%) of the result. The low case slows growth and raises the discount by `growth_spread` and `discount_spread` (default 2 percentage points each). The high case does the opposite. The reported value is the base case.

### Quality score

The score from 0 to 100 combines a simplified Piotroski F-score, an Altman Z band, and ROIC or ROE. Altman Z below 1.81 is a distress flag. Financials skip Altman Z. A median return below 8% for at least two years is a low-return flag. Strong Value and Moderate Value require `scoring.quality.minimum` (default 40). The composite score weights margin of safety 0.50, relative cheapness 0.25, and quality 0.25. How the sector comparison works is in [docs/scoring.md](docs/scoring.md). Change the weights under `scoring.weights`. A weight of 0 drops that part out.

### Commodity NAV

Energy and mining names, matched by sector or industry, get a separate figure. Utilities and financials are excluded. Median free cash flow is scaled by `planning price / reference price`, then discounted with the same DCF. The planning price is `commodity.prices.*.normalized`. The reference price is the price assumed to sit inside trailing cash flow. The sensitivity table is 0.70× through 1.30× the planning price, at the discount rates in `commodity.sensitivity_discount_rates`. This is not a reserve NAV. It ignores reserves, royalties, hedges, and the cost curve, and it does not change the composite score.

A low-cost flag is separate from the signal. It requires quality of at least `commodity.low_cost_min_quality` (60), positive free cash flow, debt/equity at or below `commodity.low_cost_max_debt_equity` (0.50), and no distress or persistently low returns. It is not an all-in sustaining cost.

## How a signal is chosen

- **Strong Value** — passes the screen, median margin at least `valuation.mos.strong` (40%), quality score at least 40, and the going-concern methods are not split across the price.
- **Moderate Value** — passes the screen, median margin at least `valuation.mos.moderate` (30%), quality score at least 40, or the methods disagree at a larger headline discount.
- **Deep Value / Net-Net** — price is below NCAV, the liquidity floors hold, and the earnings methods do not independently support a higher label. Financials are excluded. The quality floor does not block a net-net.
- **Watchlist** — the multiples pass, but the discount is thin, or the discount is large and the quality score is below the minimum.
- **Avoid** — the screen fails, the stock is expensive, or distress is not offset by a net-net. The first reason is a short line such as `Graham Number 47% discount + positive FCF + low debt + quality 74`.

A name can fail the earnings screen and still be a net-net. It cannot fail the market-cap or volume floors and still be recommended. `screens.deep_value_bypass` lets a stock below NCAV skip the earnings and leverage filters. The liquidity floors still apply.

## Configuration

`config.yaml` is the place to change the assumptions. Missing keys keep the defaults in `src/config.py`.

| Setting | Default | What it changes |
| --- | --- | --- |
| `universe.default` | `sample` | `sample`, `sp500`, a ticker list, or a file of tickers |
| `universe.sample` | the list in the file | Names used when the universe is `sample` |
| `universe.cache_ttl_hours` | 24 | How long a fetched ticker is reused |
| `valuation.dcf.discount_rate` | 0.10 | Discount rate on equity free cash flow |
| `valuation.dcf.growth_rate` | 0.03 | Growth during the explicit forecast |
| `valuation.dcf.terminal_growth` | 0.025 | Growth used in the terminal value |
| `valuation.dcf.years` | 8 | Length of the explicit forecast |
| `valuation.dcf.max_terminal_share` | 0.65 | Cap on the terminal value's share of the result |
| `valuation.graham_growth_percent` | 5 | Growth input to the revised Graham formula |
| `valuation.mos.strong` | 0.40 | Margin of safety required for Strong Value |
| `valuation.mos.moderate` | 0.30 | Margin of safety required for Moderate Value |
| `valuation.mos.minimum` | 0.30 | Floor for the ranked table |
| `scoring.quality.minimum` | 40 | Quality floor for Strong and Moderate |
| `scoring.weights` | 0.50 / 0.25 / 0.25 | Margin of safety, cheapness, quality |
| `screens.thresholds` | P/E 15, P/B 1.5, debt/equity 1.0 | Screen ceilings. Turn any screen off under `screens.enabled` |
| `screens.thresholds.pe_mode` | `absolute` | `sector_percentile` keeps the cheap tail of each sector |
| `fred.fallback_aaa_yield` | 6.0 | AAA yield, in percent, when FRED does not answer |
| `backtest.benchmark` | `SPY` | Extra price series on the historical screen |
| `news.enabled` | `true` | Fetch recent Yahoo headlines and flag very negative titles |
| `news.downrank` | `false` | When true, subtract `news.penalty` (default 8) from the composite score |
| `news.lookback_days` | 14 | How recent a dated headline must be |
| `commodity.prices` | oil 70 vs 80, gold 2000 vs 2300, copper 4.0 vs 4.2 | Planning price and the price assumed inside trailing cash flow |
| `portfolio.max_sector_weight` | 0.25 | Largest share of the suggested basket in one sector |
| `portfolio.max_position_size` | 0.10 | Largest share of the suggested basket in one name |
| `portfolio.min_holdings` | 8 | Fewest names the basket tries to hold |

`pe_mode: sector_percentile` uses `max_pe_sector_percentile` (default 0.40). A sector with fewer than five names falls back to the absolute P/E ceiling.

## Data source

Yahoo Finance is the default and does not need a key. [Financial Modeling Prep](https://site.financialmodelingprep.com/) is optional. When a key is set, a Yahoo rate limit or failure falls back to FMP, and `data.source: auto` uses FMP as the primary source. The environment variable wins over the file:

```bash
export FMP_API_KEY=your_key
```

```yaml
data:
  source: auto          # yfinance, fmp, or auto
  fmp_api_key: your_key # used only when FMP_API_KEY is unset
  fmp_api_key_env: FMP_API_KEY
```

The key is never written to the log. The AAA yield is cached for 24 hours.

## Historical screen

Each fetch is kept by calendar day. A later refresh does not erase the earlier day. `--as-of YYYY-MM-DD` reruns the screener on the latest fundamentals stored on or before that date, using the latest stored close on or before that date. A snapshot from after the date is ignored. The run does not replace the snapshot the dashboard reads.

```bash
python -m src.main --as-of 2024-01-15 --min-mos 0.30 --min-quality 40
python -m src.main --as-of 2024-01-15 --fetch-prices
```

`--fetch-prices` downloads daily closes from Yahoo from a few days before the date through today. The summary is the equal-weight 6-month and 1-year price return of the names that cleared the filters, next to the equal-weight return of every name valued that day. The benchmark ticker is shown when its closes are stored. A close counts only when it falls within `backtest.price_tolerance_days` of the horizon. There is no position size, rebalance, dividend, or trading cost.

The same screen is on the dashboard's Historical page.

## Suggested basket

After the ranking, the scan prints a basket and the dashboard shows one under the table. Names are taken in rank order under the sector cap, the position cap, and the minimum number of holdings. Weight that cannot be placed is left unallocated. The rules and a worked example are in [docs/portfolio.md](docs/portfolio.md).

```bash
python -m src.main --universe sample --max-sector-weight 0.25 --max-position 0.10 --min-holdings 8
```

The three flags override `portfolio` in `config.yaml`. The basket is a sketch of the current ranking. It is not an optimizer, and it is not investment advice.

## News

A scan stores up to eight recent Yahoo Finance titles with the fundamentals and flags very negative ones. The flag is a reason, a News column, and a warning on the stock detail. It does not change the signal label unless you set `news.downrank: true`, which subtracts `news.penalty` points (default 8) from the composite score. The word list and the lookback rules are in [docs/news.md](docs/news.md). Titles are vendor headlines, not filings.

## Logging

The terminal defaults to INFO. `logs/value_signals.log` defaults to DEBUG.

```bash
python -m src.main --universe sample --log-level DEBUG
python -m src.main --universe sample --log-level WARNING --log-file /tmp/value-signals.log
```

`--log-level` changes the console only. `logging.file_level` sets the file.

## Tests

The suite is offline. `tests/fixtures.py` holds a small universe: a healthy industrial, negative earnings, missing statements, very high debt, and zero book value. Nothing in `pytest` calls Yahoo, Financial Modeling Prep, or FRED.

```bash
source .venv/bin/activate
pytest
```

## Caveats

- This is not financial advice and not a trading system. A high score is a candidate for reading the filings.
- No formula here is a true value. The conservative defaults are part of the method. A 3% growth rate and a 10% discount rate are assumptions you have to believe, or change on purpose in `config.yaml`.
- Cheap for a reason is the usual failure. The screen catches leverage, thin liquidity, losses, and weak returns. It does not catch a dying product, a dishonest management, or a one-time cash flow.
- Yahoo's line items move around. A missing field fails the filter that needs it. It is not treated as a pass.
- Free cash flow here is equity cash flow. It is a poor description of a bank or an insurer, and the notes say so when the DCF is still shown.
- The S&P 500 list is the current index. A historical screen includes only names that were stored on or before that date, so delistings are missing. Returns ignore dividends and costs.
- Commodity sensitivity is a cash-flow scale, not a model of reserves, costs, or hedges.
- The news flag is a word list on Yahoo headlines. It misses context, and a headline can name a company that is not the subject. It is not a filing review.
- The suggested basket only caps sector weight and position size. It ignores correlation, taxes, and how much cash you actually have.
- Value signals are low frequency. Nothing in this project is meant to be traded on a daily horizon.
- Do your own work. Management, the filings, and the competitive position are still required. The machine's job stops at the shortlist.

## Later work

- Walk-forward results that include delisted names, dividends, and sizing beyond the sector and position caps.
- Maintenance capex separated from growth capex before the DCF.
- Direct links to the latest 10-K and a short review checklist per name.
- A reserve NAV for miners and oil producers, instead of a cash-flow scale.
