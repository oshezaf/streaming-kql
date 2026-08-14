# Testing

`streaming-kql` is verified by a **data-driven conformance suite**. Every test is
a YAML case describing a query, its input records, and the expected output (or
expected error). Adding coverage is a matter of dropping a YAML file — **no
Python required**.

The runner ([tests/test_cases.py](../tests/test_cases.py)) discovers every
`*.yaml` file under `tests/cases/`, turns each case into one parametrized
`pytest` test, and exercises the **public API** (`streaming_kql.compile` →
`Query.transform`). This mirrors the specification in
[SPEC.md](SPEC.md#7-test-suite-first-class-requirement) §7.

## Running the suite

```bash
pip install -e ".[dev]"     # once, in a virtual environment
pytest                       # run every case
ruff check . && mypy         # lint + type-check
```

Useful invocations:

```bash
pytest tests/test_cases.py -q                 # quiet
pytest -k mvexpand                            # only cases whose id matches "mvexpand"
pytest -k "join and leftouter"                # combine filters
pytest --cov=streaming_kql                     # with coverage
```

Each case id is `"<relative-path>::<name>"`, e.g.
`operators/join.yaml::join_inner_constant_table`, so `-k` can target a file, a
category, or a single case.

## Case format

A case is one YAML mapping. Files hold a list of cases:

```yaml
- name: extend_strcat            # required — unique, human-readable id
  query: |                       # required — the KQL source (starts with source/print/let/…)
    source | extend Full = strcat(First, " ", Last)
  schema: {First: string, Last: string}     # optional — declared KQL column types
  options: {now: "2026-01-01T00:00:00Z"}     # optional — Options (fixed clock, strict_types, …)
  input:                                       # required for value cases — list of records
    - {First: Ada, Last: Lovelace}
  expect:                                      # expected output rows (0..N)
    - {First: Ada, Last: Lovelace, Full: "Ada Lovelace"}
```

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Unique, descriptive case id. |
| `query` | yes | KQL source compiled via `kql.compile`. |
| `input` | for value cases | List of records fed one at a time to `transform`. |
| `expect` | one of `expect`/`expect_error` | The concatenated output rows across all input records. |
| `expect_error` | one of `expect`/`expect_error` | Exception class name expected at **compile** time (`KqlCompileError`, `KqlUnsupportedError`, …). |
| `schema` | no | Declared column types → coercion at ingestion. |
| `options` | no | `Options` fields (`now`, `strict_types`, …). |
| `skip` | no | Skip the case (with an optional reason). |

### Comparison semantics

Expected vs. actual records are compared **KQL-aware**, not by raw equality:

- **Column-order-insensitive** — records are compared as maps.
- **Float tolerance** — floating-point values match within
  `rel_tol = abs_tol = 1e-9` (`math.isclose`), so `0.1 + 0.2` equals `0.3`.
- Nested `dict`/`list` values are compared recursively with the same rules.

`expect_error` cases assert that `kql.compile(query)` raises the named exception
class (compile-time rejection); no `input`/`expect` is needed.

## Categories & coverage

Cases live under `tests/cases/<category>/`. Current inventory:

| Category | File | Cases | Covers |
|---|---|---:|---|
| `operators/` | `where.yaml` | 10 | `where`/`filter` predicates, null handling |
| | `extend_project.yaml` | 6 | `extend`, `project`, `project-away`/`-keep`/`-reorder`/`-rename` |
| | `parse_and_keep.yaml` | 7 | `parse`, `parse-where`, `project-keep` |
| | `let_print_parsekv.yaml` | 7 | `let`, `print`, `parse-kv` |
| | `mvexpand.yaml` | 10 | `mv-expand` (arrays/bags, multi-column, `with_itemindex`, `limit`) |
| | `summarize.yaml` | 10 | `summarize` aggregates + `by` (per-record row-set) |
| | `sort_top_take.yaml` | 11 | `sort`/`order by`, `top`, `take`/`limit`, `distinct` |
| | `join.yaml` | 14 | `join` (inner/innerunique/outer/semi/anti) vs. constant table or `source` subquery |
| | `union.yaml` | 9 | `union` of `source` subqueries and constant tables; `kind=inner/outer` |
| | `stream_tables.yaml` | 12 | `datatable`/`externaldata`/`range`, `as`/`fork`/`partition` |
| | `count_getschema.yaml` | 7 | `count`, `getschema` |
| `functions/` | `scalars.yaml` | 7 | core math scalar functions |
| | `more_scalars.yaml` | 6 | `trim*`, string/array helpers, hashing |
| | `ip_url_more.yaml` | 14 | IP (`parse_ipv4`, `ipv4_*`) and URL (`parse_url`, `url_*`) functions |
| `lookup/` | `lookup.yaml` | 10 | `lookup` against constant reference tables; `datatable` heads + coercion |
| `semantics/` | `coercion.yaml` | 13 | schema-driven type coercion (`int`/`long`/`real`/`datetime`/…) |
| | `timespan_and_xml.yaml` | 6 | timespan literals/arithmetic, `parse_xml` |
| | `dynamic_and_unsupported.yaml` | 5 | `parse_json` + dynamic access; rejection of deferred operators |
| `dcr/` | `transformations_examples.yaml` | 2 | verbatim Azure Monitor transformation doc examples |
| **Total** | | **166** | |

> The CI gate asserts that every operator/function marked ✅ in
> [SPEC.md](SPEC.md) Appendix A has at least one case. Experimental operators
> (`serialize`, `mv-apply`, `sample`, `sample-distinct`) run but are **not yet**
> in the suite; deferred operators (`scan`, `top-nested`, `make-series`) are
> covered by rejection cases in `semantics/dynamic_and_unsupported.yaml`.

## Adding a case

1. Pick (or create) the right `tests/cases/<category>/*.yaml` file.
2. Append a case with `name`, `query`, `input`, and `expect` (or
   `expect_error`).
3. Run `pytest -k <your-name>` to confirm it passes.

Every operator or function added to the supported set **must** ship at least one
case — see [CONTRIBUTING.md](../CONTRIBUTING.md) and the Appendix A tracker.
