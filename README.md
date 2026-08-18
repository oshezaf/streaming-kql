# streaming-kql

**Evaluate Kusto Query Language (KQL) over a stream of independent events.
Pure Python, no external runtime.**

Existing ways to run KQL need a cloud service, a local container, a .NET engine,
or a JVM. `streaming-kql` runs KQL entirely in-process in Python. Each input
event is evaluated independently, while operators can expand it into a bounded
row set, aggregate or reorder those rows, and combine constant, subquery, and
named tables within that event's execution context.

> **Status: alpha.** The engine supports row, expansion, aggregation, ordering,
> branching, and bounded table-combination operators, including `summarize`,
> `join`, `union`, `mv-apply`, `make-series`, and the tabular `case` extension.
> Processing remains stateless across input events. See the
> [full specification](https://github.com/oshezaf/streaming-kql/blob/main/docs/SPEC.md)
> and [supported KQL reference](https://github.com/oshezaf/streaming-kql/blob/main/docs/supported-kql.md).

## Install

```bash
pip install streaming-kql
# dev:
pip install -e ".[dev]"
```

## Quickstart

```python
import streaming_kql as kql

q = kql.compile("source | where Price > 80 | extend Note = strcat(Symbol, ' high')")

q.match({"Symbol": "MSFT", "Price": 90})
# {'Symbol': 'MSFT', 'Price': 90, 'Note': 'MSFT high'}

q.match({"Symbol": "MSFT", "Price": 10})
# None   (filtered out)

# stream any iterable of records; each input yields 0..N outputs
for out in q.stream(events):
    ...
```

Run many standing queries over one feed:

```python
node = kql.Node()
node.add("high", "source | where Price > 80")
node.add("low",  "source | where Price < 10 | project Symbol, Price")
for query_name, record in node.push(event):
    ...
```

Register custom functions:

```python
@kql.function("domain_of")
def domain_of(email: str) -> str:
    return email.split("@", 1)[-1]

kql.compile("source | extend d = domain_of(From)")
```

## Documentation

Full developer documentation lives in
[`docs/`](https://github.com/oshezaf/streaming-kql/blob/main/docs/index.md):

- [Getting started](https://github.com/oshezaf/streaming-kql/blob/main/docs/getting-started.md): install, first query, and the execution model.
- [Usage & API reference](https://github.com/oshezaf/streaming-kql/blob/main/docs/usage.md): `compile`, `Query`, `Node`, `Options`,
  `Schema`, custom functions, error handling.
- [Supported KQL](https://github.com/oshezaf/streaming-kql/blob/main/docs/supported-kql.md): every operator and function accepted,
  plus what is rejected.
- [Examples](https://github.com/oshezaf/streaming-kql/blob/main/docs/examples.md): DCR and ASIM-style recipes.
- [Specification](https://github.com/oshezaf/streaming-kql/blob/main/docs/SPEC.md): formal spec, design rationale, and roadmap.

## Supported KQL

The baseline is the full **Azure Monitor transformations (DCR) KQL surface**.
Beyond it, the engine supports per-event row sets and bounded tables: one input
event can produce zero to many rows, and batch operators act on those rows
without carrying state to the next event. See
[Supported KQL](https://github.com/oshezaf/streaming-kql/blob/main/docs/supported-kql.md)
for the authoritative list.

Route each row through the first matching sub-pipeline with the tabular `case`
extension. The final sub-pipeline is the required default:

```kusto
source
| case (
    Severity >= 4, (project Alert = Message),
    Severity >= 2, (project Warning = Message),
    (project Info = Message)
)
```

## Tests

Conformance tests are **data-driven**: add a `tests/cases/**/*.yaml` file with a
`query`, `input` records, and `expect` records (or `expect_error`) — it is
discovered automatically.

```bash
pytest
```

## License

Apache-2.0. Design and initial evaluator are informed by Microsoft's Apache-2.0
[`Rx.KQL`](https://github.com/microsoft/RxKql); see
[NOTICE](https://github.com/oshezaf/streaming-kql/blob/main/NOTICE).
