# Headline check

Each scan can store a few recent Yahoo Finance titles with the fundamentals and flag a name whose titles are very negative. The check is a word list in `src/news/sentiment.py`. It is not a language model, and the titles are vendor headlines, not filings.

The flag does not change the signal label. Strong Value stays Strong Value. Set `news.downrank` to `true` if a flagged name should also sort lower.

## What gets stored

When `news.enabled` is true (the default), a successful fundamentals fetch also asks Yahoo for up to `news.max_headlines` titles (default 8). A news failure is logged and the fundamentals are kept. The headlines travel with that snapshot, so the dashboard does not request them again.

The lookback is `news.lookback_days` (default 14), measured from the time the snapshot was fetched. A title older than that window is ignored. A title with no timestamp is treated as recent, because Yahoo returns the latest stories first.

## What gets flagged

A title is ignored when it contains none of the listed words.

**One severe word is enough.** The severe list is bankruptcy, bankrupt, fraud, fraudulent, indictment, indicted, insolvency, insolvent, delisting, delisted, scandal, and criminal.

**Milder words need two headlines.** A single downgrade, layoff, lawsuit, probe, recall, plunge, guidance cut, or similar word does not flag the name. Two such headlines inside the window do. The moderate list is in `src/news/sentiment.py`.

**A positive word in the same title offsets the hit.** Positive words include beats, beat estimates, upgrade, record profit, raises guidance, raised guidance, dividend increase, buyback, surges, and soars. `Acme beats bankruptcy fears and soars` is not flagged. `Acme files for bankruptcy protection` is flagged.

Matching uses whole words, so `layoff` does not match a longer unrelated token.

## Where you see it

- The reason list: `Very negative recent headlines: "…"`.
- The CLI table: a News column with `Neg`, or blank.
- The dashboard: a News column on the ranked table, and a warning on the stock detail that quotes the titles.

## Down-ranking

`news.downrank` defaults to false, so the composite score is unchanged and only the flag is shown. Set it to true to subtract `news.penalty` points (default 8) from the composite score. The score does not go below 0. The signal label is still unchanged. The reason list says the score was reduced.

```yaml
news:
  enabled: true
  lookback_days: 14
  max_headlines: 8
  min_negative_headlines: 2
  downrank: false
  penalty: 8
```

Turn `enabled` off to skip the Yahoo news request and ignore any headlines already stored on the stock.

## Limits

The list misses context. A headline can name a company that is not the subject. "Warning" and "default" are broad. A story with no listed word is invisible to the check. This is a prompt to read the article, not a judgment on the business.
