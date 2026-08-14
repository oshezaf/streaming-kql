# streaming-kql documentation

**Evaluate the stateless subset of the Kusto Query Language (KQL) over a stream
of events — one record at a time. Pure Python, no external runtime.**

`streaming-kql` runs the common, high-value part of KQL — **filter, reshape, and
enrich each event** — entirely in-process in Python, with the same shape as an
Azure Monitor [**DCR transformation**](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql)
or a Sentinel **ASIM parser's** per-row logic. There is no cloud service, no
container, no .NET engine, and no JVM.

## Documentation map

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, first query, the mental model |
| [Usage & API reference](usage.md) | `compile`, `Query`, `Node`, `Options`, `Schema`, custom functions, error handling |
| [Supported KQL](supported-kql.md) | Every operator, scalar operator, and function the engine accepts, plus what is rejected |
| [Examples](examples.md) | End-to-end recipes: DCR transformations, ASIM-style parsing, enrichment |
| [Testing](testing.md) | The data-driven YAML conformance suite: how to run it, the case format, and coverage |
| [Specification](SPEC.md) | The formal spec, design rationale, and roadmap |

## The one-sentence model

> A query is applied to **each record individually**. Only operators that take a
> single row as input are supported; each input row yields **zero or more output
> rows** with no dependence on other rows, ordering, or accumulated state.

- **1 → 0** — `where` filters the record out.
- **1 → 1** — `extend`, `project`, `parse`, … reshape the record.
- **1 → N** — `mv-expand`/`union` fan one record into several rows, which
  `summarize`/`sort`/`top`/`distinct`/`take` can then aggregate or reorder
  **within that record's row-set**.

Aggregation, ordering, and dedup work **per input record** (never across the
stream), and `join`/`union` work against a **bounded** right side (a constant
table or a same-record `source` subquery). Only a genuinely *cross-record*
operation — a temporal two-stream `join` — is out of scope (and isn't even
expressible, since `source` denotes the current record). See
[Supported KQL](supported-kql.md).

## 30-second example

```python
import streaming_kql as kql

q = kql.compile("source | where Price > 80 | extend Note = strcat(Symbol, ' high')")

q.match({"Symbol": "MSFT", "Price": 90})
# {'Symbol': 'MSFT', 'Price': 90, 'Note': 'MSFT high'}

q.match({"Symbol": "MSFT", "Price": 10})
# None   (filtered out)
```

## Status

**Alpha.** The engine supports `let`/`print`/`source`, `where`/`filter`,
`extend`, `project` (and `-away`/`-keep`/`-reorder`/`-rename`), `parse`/
`parse-where`, `parse-kv`, `evaluate bag_unpack`, `mv-expand`, `union` (of
`source` subqueries and constant tables), `lookup` and `join` (all kinds) against
constant `datatable`/`externaldata` tables or same-record `source` subqueries,
per-record `summarize`/`sort`/`top`/`distinct`/`take`/`partition`, `as`/`fork`
for naming stream slices, `range` as a source, schema-driven type
coercion, a full scalar-expression grammar, and a large built-in scalar-function
library. See [Supported KQL](supported-kql.md) for the authoritative list and
[SPEC.md](SPEC.md) §9 for the roadmap.

## License

Apache-2.0. Design and initial evaluator are informed by Microsoft's Apache-2.0
[`Rx.KQL`](https://github.com/microsoft/RxKql); see [NOTICE](../NOTICE). KQL
semantics follow the [official Microsoft KQL documentation](https://learn.microsoft.com/en-us/kusto/query/).
