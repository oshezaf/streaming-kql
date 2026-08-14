# Getting started

## Requirements

- Python **3.10+**
- No external runtime — the only core dependency is the pure-Python
  [`lark`](https://github.com/lark-parser/lark) parser.

## Install

```bash
pip install streaming-kql          # after first release
```

From a checkout, for development:

```bash
pip install -e ".[dev]"
```

## Your first query

A query is compiled once and then evaluated against many records. The input
table is always called `source`.

```python
import streaming_kql as kql

q = kql.compile("source | where Price > 80 | extend Note = strcat(Symbol, ' high')")

q.match({"Symbol": "MSFT", "Price": 90})
# {'Symbol': 'MSFT', 'Price': 90, 'Note': 'MSFT high'}

q.match({"Symbol": "MSFT", "Price": 10})
# None   (filtered out — where removed the record)
```

## The mental model

`streaming-kql` evaluates the **stateless** subset of KQL. Each record is
processed on its own:

- The query runs against **one record at a time** (a Python `dict`).
- Each input record produces **zero or more** output records.
- There is **no state** carried between records — no ordering, no aggregation,
  no joins.

This is exactly the contract of an Azure Monitor
[**DCR transformation**](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql):
"single row in → zero or one row out." `streaming-kql` targets that surface and
the remaining stateless operators.

| Cardinality | Example | Result |
|---|---|---|
| 1 → 0 | `source \| where Price > 80` on `{Price: 10}` | `[]` (dropped) |
| 1 → 1 | `source \| extend x = Price * 2` | one reshaped record |
| 1 → N | `source \| evaluate bag_unpack(Ctx)` | columns added from a bag |

Operators that need to look across records — `summarize`, `join`, `sort`,
`top`, `distinct`, … — are **rejected at compile time** with a
`KqlUnsupportedError`. This is intentional: the engine tells you what it cannot
do rather than silently producing wrong results.

## Three ways to run a query

### `match` — for 1 → 0/1 queries

Returns a single `dict`, or `None` if the record was filtered out. Raises if the
query produced more than one record.

```python
q = kql.compile("source | where Level >= 3")
q.match({"Level": 4})   # -> {'Level': 4}
q.match({"Level": 1})   # -> None
```

### `transform` — for the general 0..N case

Always returns a `list[dict]`.

```python
q.transform({"Level": 4})   # -> [{'Level': 4}]
q.transform({"Level": 1})   # -> []
```

### `stream` — for a feed

Lazily processes an iterable of records, yielding each output record.

```python
for out in q.stream(events):
    handle(out)
```

## Next steps

- [Usage & API reference](usage.md) — every public class and function.
- [Supported KQL](supported-kql.md) — the full list of what you can write.
- [Examples](examples.md) — DCR and ASIM-style recipes.
