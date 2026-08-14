# streaming-kql

**Evaluate the stateless subset of the Kusto Query Language (KQL) over a stream
of events — one record at a time. Pure Python, no external runtime.**

Existing ways to run KQL need a cloud service, a local container, a .NET engine,
or a JVM. `streaming-kql` runs the common, high-value part of KQL — **filter,
reshape, and enrich each event** — entirely in-process in Python, with the same
shape as an Azure Monitor **DCR transformation** or a Sentinel **ASIM parser's**
per-row logic.

> **Status: alpha (M0).** The engine currently supports `where`/`filter`,
> `extend`, `project`, `project-away`, `project-rename`, a scalar-expression
> grammar, and a starter scalar-function set. Stateful operators (`summarize`,
> `join`, `sort`, …) are rejected at compile time. See [docs/SPEC.md](docs/SPEC.md)
> for the full specification and roadmap.

## Install

```bash
pip install streaming-kql          # (after first release)
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

Full developer documentation lives in [`docs/`](docs/index.md):

- [Getting started](docs/getting-started.md) — install, first query, the model.
- [Usage & API reference](docs/usage.md) — `compile`, `Query`, `Node`, `Options`,
  `Schema`, custom functions, error handling.
- [Supported KQL](docs/supported-kql.md) — every operator and function accepted,
  plus what is rejected.
- [Examples](docs/examples.md) — DCR and ASIM-style recipes.
- [Specification](docs/SPEC.md) — formal spec, design rationale, roadmap.

## Supported KQL

The target is the full **Azure Monitor transformations (DCR) KQL surface**
(single row in → ≤ one row out) plus all remaining **stateless** operators. See
[docs/supported-kql.md](docs/supported-kql.md) for the authoritative list, and
[docs/SPEC.md](docs/SPEC.md) §5 with the implementation tracker in Appendix A.

## Tests

Conformance tests are **data-driven**: add a `tests/cases/**/*.yaml` file with a
`query`, `input` records, and `expect` records (or `expect_error`) — it is
discovered automatically.

```bash
pytest
```

## License

Apache-2.0. Design and initial evaluator are informed by Microsoft's Apache-2.0
[`Rx.KQL`](https://github.com/microsoft/RxKql); see [NOTICE](NOTICE).
