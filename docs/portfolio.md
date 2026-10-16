# Suggested basket

The basket turns the current ranking into a short list of weights. It walks the names in the order they are already ranked and takes the next one that still fits. It does not re-score names, and it does not optimize for return, risk, or correlation.

It is a sketch of the reading list. It is not investment advice, and it is not an instruction to buy.

## The three caps

| Setting | Default | Meaning |
| --- | --- | --- |
| `portfolio.max_sector_weight` | 0.25 | One sector can be at most 25% of the basket |
| `portfolio.max_position_size` | 0.10 | One name can be at most 10% of the basket |
| `portfolio.min_holdings` | 8 | The basket tries to hold at least this many names |

A blank sector is treated as `Unknown`, and every blank sector shares that one bucket, so missing labels cannot bypass the sector cap.

## How a weight is chosen

Every holding starts with the same slot. The slot is the smallest of:

- the position cap
- the sector cap
- `1 / minimum holdings`

With the defaults, the slot is `min(10%, 25%, 1/8) = 10%`. The book wants 10 names at 10% each. A 25% sector cap leaves room for two of those names (20%). The third name in that sector is skipped, and the walk takes the next name from another sector.

If the minimum count is the tighter constraint, the slot shrinks. Eight holdings with a 20% position cap and a 25% sector cap use a slot of 12.5%, because `1 / 8` is smaller than 20%. Eight names then fill the book.

After the minimum count is met, the walk keeps adding full slots until the book is full. The last few percent can go to one more name when that remainder still fits under both caps. A name is not given a sliver just to use up a sector that is almost full, while a full slot is still required.

Weight that cannot be placed stays unallocated. The note says which of these happened:

- The ranked list has no further names.
- Another holding would break a position or sector cap.
- Fewer names fit than the minimum.

Duplicate tickers are taken once. The first copy in the ranked list wins.

## Where it shows up

The CLI prints the basket after the ranked table. These flags override `config.yaml` for that run:

```bash
python -m src.main --universe sample --max-sector-weight 0.25 --max-position 0.10 --min-holdings 8
```

The dashboard builds the basket from the **filtered** table, under the ranked names. The three sliders start from `config.yaml` and apply immediately. They do not start a new scan. Download basket CSV saves the rows on screen: ticker, name, sector, signal, score, and weight.

A historical screen does not print a basket. Build one from the dashboard filters, or from the ranked rows of a normal scan.

## A filled book

Five sectors, three names each, ranked energy first. Caps are 25% sector, 10% position, and 8 holdings. The slot is 10%, so each sector contributes its two highest names. The third name in every sector is skipped. The basket holds 10 names, invested 100%, with 20% in each sector. The first row is the highest-ranked energy name.

## A short list

Two ranked names in different sectors, same default caps. Each gets 10%. Invested weight is 20%. The notes say that 2 holdings fit and the minimum is 8, and that 80% is unallocated because the ranked list has no further names.

On a snapshot whose filtered table is only two Strong Value names in the same sector, the same thing happens: two positions at 10%, and the rest unallocated. Widen the filters, or lower the minimum, if you want a fuller sketch. Raising the position size does not invent names the ranking does not contain.

## What the caps leave out

The basket ignores correlation inside a sector, taxes, lot size, and how much cash you have. Two oil producers and a refiner can all sit under the energy cap and still move together. Unallocated weight means the caps or the list ran out. It is not a recommendation to hold cash.
