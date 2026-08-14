# Changelog

All notable changes to **streaming-kql** are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/).

## [0.0.1] - 2026-08-14

Initial release: a pure-Python engine that evaluates the **stateless, per-record
subset of KQL** over a stream of events — one record at a time, with no external
runtime. See [docs/](docs/index.md) for the guide and
[docs/supported-kql.md](docs/supported-kql.md) for the authoritative feature list.

### Highlights

- **Per-record model.** A query is applied to each input record independently and
  emits 0..N records. A "table" is the current record's row-set, so operators KQL
  treats as stateful have a well-defined *stateless per-record* form here.
- **Row operators:** `where`/`filter`, `extend`, `project` (+ `-away`/`-keep`/
  `-reorder`/`-rename`), `parse`/`parse-where`, `parse-kv`, `evaluate bag_unpack`,
  `mv-expand`, `lookup`.
- **Batch operators** over the per-record row-set: `summarize` (count/sum/avg/min/
  max/dcount/make_list/make_set/countif/… with `by`), `sort`/`order by`, `top`,
  `distinct`, `take`/`limit`, `count`, `getschema`, `union`, and `join` (all kinds
  against a bounded right side), plus `partition`, `as`, and `fork` for splitting
  and naming stream slices. Also `serialize` with window functions
  (`row_number`/`prev`/`next`/`row_cumsum`), `mv-apply`, `make-series`, and
  `sample`/`sample-distinct` (seedable via `Options(random_seed=…)`).
- **Sources & reference tables:** `source`, `print`, `datatable`, `range`, and
  `externaldata` (local files only — the library stays offline).
- **Scalars:** a full expression grammar (arithmetic, comparisons, string
  operators incl. `has`/`contains`/`startswith`/`endswith` with `_cs`/negation,
  `has_any`/`has_all`, `in`, `matches regex`) and a large built-in scalar-function
  library (string, conversion, datetime/timespan, dynamic/array, IP/URL, math,
  hashing, conditional). Custom functions via `@kql.function`.
- **Type coercion:** an optional `Schema` coerces declared input columns to their
  KQL type before the query runs.
- **API:** `compile()`, `Query.transform`/`match`/`stream`, the `Node` multi-query
  host, `Options`, `Schema`, and a typed error hierarchy
  (`KqlError`/`KqlCompileError`/`KqlUnsupportedError`/`KqlEvalError`).
- **Testing:** a data-driven suite where each case is a YAML file auto-discovered
  by `tests/test_cases.py`.

### Not yet implemented

Two operators with a per-record form are recognized but not built yet: `scan`
(row-by-row state machine) and `top-nested` (hierarchical top). A *temporal* join
of two independent streams is out of scope (a future stateful extension).
Compiling any of these raises `KqlUnsupportedError`.

