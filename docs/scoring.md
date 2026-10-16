# Sector-relative scoring

The composite score is a number from 0 to 100. It is the weighted average of three parts: margin of safety, relative cheapness, and quality. The default weights are 0.50, 0.25, and 0.25. A weight of 0 drops that part out. The weights live under `scoring.weights` in `config.yaml`.

This is a research score. It is not a forecast and not investment advice.

## What "relative" means

P/E, P/B, and EV/EBIT are ranked inside the stock's **sector**. A sector with fewer than five names uses the **industry** instead. A group that is still smaller than five names does not get a percentile. That stock falls back to absolute multiple buckets.

The whole universe is not the peer set. A bank with a P/E of 8 is cheap only when 8 is cheap next to other banks. The reason line names the group, for example `P/E is cheaper than 80% of its sector`.

A missing sector label does not join an "Unknown" bucket. Those names can still use their industry when that industry has five or more names.

The percentile itself is the share of peers with a lower multiple. **0 is the cheapest name in the group. 1 is the most expensive.** The cheapness input to the score is one minus that percentile, averaged across whichever of P/E, P/B, and EV/EBIT have a percentile. A metric with no positive denominator is left out of the average.

The P/E screen can still use a sector-only percentile when `screens.thresholds.pe_mode` is `sector_percentile`. That screen figure stays sector-only. It does not switch to the industry. A sector with fewer than five names falls back to the absolute P/E ceiling.

## Worked example

Five energy names have P/E ratios of 10, 20, 30, 40, and 50. Five industrials have P/E ratios of 5, 6, 7, 8, and 9.

On a universe ranking, the energy name at a P/E of 10 looks middling, because five industrials are cheaper. Inside Energy it is the cheapest name, so its sector percentile is 0 and its cheapness contribution is 1. The energy name at a P/E of 50 is the most expensive in its sector, so its percentile is 1 and its cheapness contribution is 0. The industrials do not pull the energy names up or down.

## Absolute fallback

When a group is too small, or a multiple is missing, cheapness uses fixed buckets:

| Multiple | Full cheapness | None |
| --- | --- | --- |
| P/E | below 8 | 20 or higher |
| P/B | below 0.8 | 2.5 or higher |
| EV/EBIT | below 6 | 14 or higher |

Values between those ends step down rather than falling off a cliff. A P/E of 12 is a partial score, not a zero. The three multiples are averaged. A single-name test universe always takes this path, because five peers are required.

## The other two parts

Margin of safety stays an absolute discount: `(intrinsic value − price) / intrinsic value`. The headline value is the median of the methods that returned a number. Inside the score, a margin of −20% maps to 0 and a margin of +60% maps to 1. Values outside that range sit at the ends.

Quality is also absolute, from 0 to 100. It blends a simplified Piotroski F-score (weight 0.40), an Altman Z band (0.30), and ROIC or ROE (0.30). Altman Z below 1.81 is a distress flag. Financials skip Altman Z. A median return below 8% for at least two years is a low-return flag. Missing inputs score 50 rather than failing the scan.

Strong Value and Moderate Value still require `scoring.quality.minimum` (default 40) and the margin-of-safety floors in `valuation.mos`. A cheap percentile does not override a failed screen or a thin discount.

## When the score changes

Peer groups are built while a scan or a historical screen values the universe. The dashboard reads the stored score. Editing `config.yaml` or the peer rule does not reshuffle the table until the next scan.

Commodity NAV is a separate figure. It is not one of the three score inputs.
