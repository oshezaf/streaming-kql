# streaming-kql — Specification

> **Spec v0.1 (2026-08-10).** A pure-Python library that evaluates the
> **stateless subset of the Kusto Query Language (KQL)** against events **one
> record at a time** (streaming / per-event), with **no storage, no database,
> and no non-Python runtime**. Distribution name **`streaming-kql`**, import
> package **`streaming_kql`**. License **Apache-2.0**.

---

## 1. Motivation & goals

Running KQL today requires a cloud service (Azure Data Explorer, Sentinel, Log
Analytics), a local container (Kusto emulator), a .NET engine (KustoLoco,
Rx.KQL), or a JVM. There is **no pure-Python engine that evaluates KQL over a
stream of events in-process.** `streaming-kql` fills that gap for the common,
high-value case: **filter + reshape + enrich each event with KQL**, the same
shape as an Azure Monitor **DCR transformation** or a Sentinel **ASIM parser's**
per-row logic.

### 1.1 Goals

1. **Pure Python, pip-installable, zero external runtime** (no .NET/JVM/Docker).
2. **Streaming / per-record**: evaluate a compiled query against one event
   (a `dict`) and emit 0..N output events, statelessly.
3. **KQL fidelity for the stateless subset** — at minimum the full **Azure
   Monitor transformations (DCR) KQL surface** (§5), itself defined as "single
   row in → ≤ one row out." Beyond that, all remaining **stateless** operators
   are in scope (subject to per-operator feasibility, §5.2).
4. **Correctness by construction**: a **data-driven, extensible test suite** (§7)
   where a contributor adds a case by dropping an input/expected file.
5. **Extensible**: register custom scalar functions; pluggable parser.
6. **A clean public library** for GitHub + PyPI.
7. **Designed to grow — including to stateful** (§2.3, §6): the architecture must
   not preclude a later stateful-operator extension.

### 1.2 Non-goals (initially)

- **Stateful / multi-record operators** — `summarize`, `join`, `union`, `sort`/
  `order`, `top`, `make-series`, `serialize`, `partition`, `scan` (§5.6). These
  need buffering/ordering and are out of the streaming stateless core. A later
  **stateful extension** may add windowed variants (§2.3).
- A full Azure Data Explorer engine, storage, or cluster semantics.
- `geo_location` (external IP-geolocation service) — **not planned** (out of
  scope; it requires a network service and does not fit an offline library).
- Query optimization beyond what correctness and per-event speed require.

### 1.3 Relationship to prior art

The engine's design and initial evaluator are informed by Microsoft's
[`Rx.KQL`](https://github.com/microsoft/RxKql) (Apache-2.0), whose per-event core
is exactly `where`/`project`/`extend` over an `IDictionary<string,object>`.
`streaming-kql` reimplements this in Python, **drops the Rx.Net streaming host**
(unneeded — the caller drives records), and extends operator/function coverage to
the DCR baseline. Attribution is in `NOTICE`.

---

## 2. Scope: the stateless streaming model

The organizing invariant (from Azure Monitor transformations, verbatim intent):

> **A query is applied to each record individually. Only operators that take a
> single row as input are supported; each input row yields zero or more output
> rows with no dependence on other rows, ordering, or accumulated state.**

- **1 → 0**: `where` filters the record out.
- **1 → 1**: `extend`, `project`, `parse`, … reshape the record.
- **1 → N**: `mv-expand` (stateless expansion) — **deferred**, evaluated
  per-operator (§5.2); the model *allows* it (`transform` already returns a list)
  but each 1→N operator is assessed individually for feasibility/semantics.

### 2.1 Inputs

A **record** is a `Mapping[str, Any]` (typically `dict`). An optional **schema**
declares column KQL types.

### 2.2 Outputs

`transform(record) -> list[dict]` (0..N). Convenience: `match(record) -> dict |
None` for 1→≤1 queries; `stream(iterable) -> Iterator[dict]` for a feed.

### 2.3 Future: stateful (design constraint, not v1 scope)

A later opt-in **stateful extension** (separate module/flag) may add windowed
`summarize`, bounded `sort`/`top`, `join` against a reference set, etc. The core
architecture (§6) keeps scalar evaluation independent of the operator layer and
models operators as record→records so a stateful operator layer (operator holds
state across the stream) can be added **without changing the scalar engine, the
parser, or the public streaming API**. Until then, stateful operators are
rejected at compile time with a clear error.

---

## 3. Data model

### 3.1 Records

A record is an ordered `dict` column→value. Column order is preserved so
`project` reorders deterministically. Outputs are new dicts (inputs never
mutated).

### 3.2 Types

| KQL type | Python | Notes |
|---|---|---|
| `string` | `str` | |
| `bool` | `bool` | |
| `int` / `long` | `int` | both map to Python `int` (see Open Q) |
| `real` / `double` | `float` | `NaN`/`±inf` supported |
| `decimal` | `decimal.Decimal` | later phase |
| `datetime` | tz-aware `datetime` (UTC) | ISO-8601 |
| `timespan` | `timedelta` | literals `1d`,`2h`,`30m`,`500ms` |
| `guid` | `uuid.UUID`/`str` | |
| `dynamic` | `dict`/`list`/scalar/`None` | JSON-like |
| null | `None` | KQL three-valued logic |

### 3.3 Schema (optional)

`Schema({"col": "datetime", "ctx": "dynamic", ...})` supplies true KQL typing/
coercion; without it, types are inferred from Python values. (In M0 the schema is
accepted and stored; coercion lands in a later milestone.)

---

## 4. Public API (inputs & outputs)

### 4.1 Compile
```python
import streaming_kql as kql
q = kql.compile(source, schema=None, options=None)   # -> Query (reusable, thread-safe)
```
Parse/semantic errors raise `KqlCompileError` (line/col). Recognized stateful
features raise `KqlUnsupportedError`.

### 4.2 Evaluate
```python
out: list[dict] = q.transform(record)   # 0..N
one: dict | None = q.match(record)       # 1→≤1 (raises if >1)
for rec in q.stream(records): ...        # lazy per-record
```

### 4.3 Multi-query host
```python
node = kql.Node()
node.add("name", "source | where ...")
for query_name, rec in node.push(record): ...
```

### 4.4 Options
`now` (fixed clock for `now()`/`ago()`), `strict_types` (raise vs. null),
(planned) `regex_engine`, `time_zone`.

### 4.5 Custom functions
```python
@kql.function("domain_of")
def domain_of(email: str) -> str: ...
```

### 4.6 Errors
`KqlCompileError`, `KqlUnsupportedError` (compile-time), `KqlEvalError`
(strict-mode runtime), base `KqlError`.

---

## 5. Supported KQL surface

Baseline = the full **Azure Monitor transformations (DCR) KQL feature set**
([reference](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql)),
plus remaining stateless operators. Tracked in Appendix A.

### 5.1 Statements
`source` (the input stream — the only table), `print` (single synthetic row),
`let` (scalar / tabular / scalar-argument user function, per DCR).

### 5.2 Tabular operators

**DCR baseline:** `where`/`filter`, `extend`, `project`, `project-away`,
`project-rename`, `parse`/`parse-where`, `print`, `datatable`, `columnifexists`.

**Stateless extensions (post-baseline):** `project-keep`, `project-reorder`.

**Deferred — evaluate individually (not committed):** `mv-expand`, `mv-expand`
with `bag_unpack`/`evaluate bag_unpack`, `take`/`limit`, `sample`. These are 1→N
or need bounded state; each will be assessed for clean stateless semantics before
inclusion. Until implemented they raise `KqlUnsupportedError` ("under
evaluation").

**Rejected (stateful, §5.6).**

### 5.3 Scalar operators
Numerical (all); datetime/timespan arithmetic (all); string `==`,`!=`,`=~`,`!~`,
`contains(_cs)`,`!contains(_cs)`,`has(_cs)`,`!has(_cs)`,`startswith(_cs)`,
`!startswith(_cs)`,`endswith(_cs)`,`!endswith(_cs)`,`matches regex`,`in`,`!in`
(+`has_any`/`has_all`); bitwise `binary_*`.

### 5.4 Scalar functions
Full DCR set (Appendix A tracks status):

- **Conversion:** `tobool`, `todatetime`, `todouble`/`toreal`, `toguid`, `toint`,
  `tolong`, `tostring`, `totimespan`.
- **DateTime/TimeSpan:** `ago`, `now`, `datetime_add`, `datetime_diff`,
  `datetime_part`, `dayofmonth`, `dayofweek`, `dayofyear`, `endofday/month/week/
  year`, `getmonth`/`monthofyear`, `getyear`, `hourofday`, `make_datetime`,
  `make_timespan`, `startofday/month/week/year`, `weekofyear`.
- **Dynamic/array:** `array_concat`, `array_length`, `pack`, `pack_array`,
  `parse_json`, `parse_xml`, `zip`.
- **Math:** `abs`, `bin`/`floor`, `ceiling`, `exp`, `exp2`, `exp10`, `isfinite`,
  `isinf`, `isnan`, `log`, `log2`, `log10`, `pow`, `round`, `sign`.
- **Conditional:** `case`, `iif`/`iff`, `max_of`, `min_of`, `coalesce`.
- **String:** `base64_encodestring`, `base64_decodestring`, `countof`, `extract`,
  `extract_all`, `indexof`, `isempty`, `isnotempty`, `parse_json`, `replace`,
  `split`, `strcat`, `strcat_delim`, `strlen`, `substring`, `tolower`, `toupper`,
  `hash_sha256`.
- **Type:** `gettype`, `isnull`, `isnotnull`.
- **Bitwise:** `binary_and/or/not/xor/shift_left/shift_right`.
- **Special (transformation-only):** `parse_cef_dictionary`. *(`geo_location` is
  not planned — §1.2.)*

### 5.5 Semantics to honor
- **`parse kind=regex` full-string match** (DCR behavior) by default; option for
  Log-Analytics partial-match.
- **Dynamic access** `col.Prop` / `col["Prop"]`; missing path → null.
- **Null propagation** / three-valued logic; `isnull`/`coalesce` per KQL.
- **Case sensitivity** for `_cs` operators and `=~`/`!~`.
- **datetime/timespan literals**.

### 5.6 Out of scope (stateful) — rejected at compile time
`summarize`, `make-series`, `join`, `union`, `sort`/`order by`, `top`,
`top-nested`, `serialize`, `row_number`, `partition`, `scan`, `range` (as source),
`distinct`, `count`, and aggregation functions. Compiling raises
`KqlUnsupportedError` naming the operator. A future stateful extension (§2.3) may
add windowed equivalents.

### 5.7 Further stateless additions to consider (survey)

Beyond the DCR baseline, these KQL features also fit the **single-event
stateless** model and are candidates for future milestones. (Anything that
aggregates, orders, or dedupes across rows is *excluded* — see §5.6.)

**Tabular operators (stateless-compatible):**

| Operator | Cardinality | Notes / priority |
|---|---|---|
| `datatable` | source, N constant rows | for `let`/tests; **planned** |
| `evaluate bag_unpack` | 1→1 | expand a `dynamic` bag into columns — high value for logs |
| `mv-expand` | 1→N | expand an array/bag column into rows (deferred; the model allows 1→N) |
| `mv-apply` (no aggregation) | 1→N | per-row subquery over an array; only the non-aggregating form |
| `evaluate narrow` | 1→N | unpivot one row to key/value rows |
| `search` (per-row) | 1→0/1 | free-text match across a row's columns |
| `sample` / `sample-distinct` | 1→0/1 | non-deterministic (random) — opt-in only |

**Not addable (stateful):** `summarize`, `join`, `union`, `sort`/`order`, `top`,
`make-series`, `serialize`, `partition`, `scan`, `range`, `distinct`, `count`,
`facet`, `render`, `lookup`.

**Scalar functions (stateless) — prioritized for log/ASIM work:**

- **IP:** `parse_ipv4`, `ipv4_is_in_range`, `ipv4_is_private`, `ipv4_compare`,
  `ipv4_netmask_suffix`, `parse_ipv6`, `ipv6_compare`, `format_ipv4`.
- **URL / path:** `parse_url`, `parse_urlquery`, `parse_path`, `url_encode`,
  `url_decode`, `url_encode_component`.
- **String:** `strcmp`, `translate`, `indexof_regex`, `parse_command_line`,
  `parse_csv`, `parse_version`, `punycode_*`, `regex_quote`.
- **Dynamic / array:** `bag_merge`, `bag_remove_keys`, `array_sort_asc`/`_desc`,
  `array_split`, `array_rotate_left`/`_right`, `array_reverse`, `array_sum`,
  `array_iff`, `treepath`.
- **DateTime:** `datetime_add`, `datetime_part`, `make_datetime`,
  `make_timespan`, `format_timespan`, `endofday`/`endofmonth`/`endofweek`/
  `endofyear`, `weekofyear`, `unixtime_seconds_todatetime` (+ ms/us/ns).
- **Math:** trig (`sin`/`cos`/`tan`/`asin`/`acos`/`atan`/`atan2`),
  `degrees`/`radians`, `gamma`/`log_gamma`, `gcd`/`lcm`, `not`. *(`rand` is
  non-deterministic → opt-in only.)*
- **Conversion:** `toguid`, `todecimal`, `tohex` (done), `tolong` (done).
- **Hash:** `hash`, `hash_md5`, `hash_sha1`, `hash_combine`, `hash_many`,
  `hash_xxhash64`.
- **Type:** `isascii`, `isutf8`.
- **Special (transformation-only):** `parse_cef_dictionary`, `parse_xml`.
- **Beyond-DCR (added):** `parse-kv` operator; `trim`/`trim_start`/`trim_end`,
  `strcat_array`, `reverse`, `sqrt`, `tohex`, `array_index_of`, `array_slice`,
  `bag_keys`, `set_union`/`set_intersect`/`set_difference`, `format_datetime`,
  `datetime_diff`, `dayofweek`.

---

## 6. Architecture

```
KQL text ─▶ lexer+parser ─▶ AST (operators + scalar expr) ─▶ semantic check
        ─▶ compiled plan (list of record→records callables) ─▶ 0..N records
```

Modules (M0 keeps them as flat modules; they may grow into subpackages):
`streaming_kql/parser.py` (**Lark** grammar + a `Transformer` that lowers the
parse tree → AST),
`nodes.py` (AST), `evaluator.py` (operator + scalar compilation, `Options`,
`CompiledQuery`), `functions.py` (scalar library + registry), `api.py` (public
surface), `errors.py`.

**Design for stateful growth.** Scalar evaluation (`env -> value`) is independent
of the operator layer (`record -> Iterable[record]`). A future stateful operator
is just an operator object that keeps state across calls and is driven by
`stream()`; nothing in the scalar engine, parser AST, or public API needs to
change. Operator dispatch and the stateful/stateless classification live in one
place (`parser._STATEFUL_OPERATORS` / evaluator dispatch) to make the extension a
localized change.

### 6.1 Parser strategy — options & trade-offs

The parser is the largest and most consequential component (it also gates how
easily the grammar grows toward stateful operators). Three options:

| Option | Pros | Cons | Fit for future stateful growth |
|---|---|---|---|
| **A. Hand-written recursive descent** *(current M0)* | Zero dependencies; full control of error messages/positions; easy to special-case KQL quirks (hyphenated operators, `matches regex`, `!op` forms); trivial to extend operator-by-operator | More code to maintain as the grammar grows; risk of ad-hoc grammar drift; no formal grammar artifact | **Good**: adding an operator = a parse branch + a node; but a large grammar (many operators/functions, `let`, `datatable`, joins later) gets unwieldy by hand |
| **B. [Lark](https://github.com/lark-parser/lark) EBNF grammar** | Pure-Python (keeps the no-runtime promise); a single declarative grammar file is easier to review/evolve; good error reporting; earley/LALR options | New dependency; must map parse tree → AST; KQL's context-sensitive bits (hyphenated op names, `has`/`contains` as operators vs. identifiers) need care | **Best**: a declarative grammar scales to the full KQL surface (and stateful operators) far better than hand-code; grammar changes are localized and diffable |
| **C. [ANTLR4](https://github.com/antlr/antlr4) with a KQL grammar (Python target)** | Could reuse an existing community/Microsoft Kusto ANTLR grammar → broad coverage quickly; industrial-strength | Heavier toolchain (Java to generate; generated Python is bulky); grammar may over-cover (full ADX) and need trimming; less ergonomic error messages | **Strong** on coverage, **weaker** on maintainability/footprint for a small library |

**Decision (2026-08-10): Lark (option B) is adopted now.** The AST and evaluator
are parser-agnostic, so the choice stays reversible, but a declarative grammar
scales best toward the full DCR surface and the future stateful extension, while
remaining pure-Python. **A (hand-written)** was used for the very first M0 spike
and has been replaced. **C (ANTLR)** remains a fallback only if a vetted KQL
grammar later makes broad coverage cheaper than maintaining the Lark grammar.

**Dependencies:** `lark` (parser) is the one core runtime dependency. Optional
later: `python-dateutil` (datetime parsing), `google-re2` (safe regex).

---

## 7. Test suite (first-class requirement)

Data-driven and extensible: adding a test = adding a YAML file; no Python needed.

### 7.1 Case format (`tests/cases/**/*.yaml`)
```yaml
- name: extend_strcat_basic
  query: |
    source | extend Full = strcat(First, " ", Last)
  schema: {First: string, Last: string}       # optional
  options: {now: "2026-01-01T00:00:00Z"}       # optional
  input:  [{First: Ada, Last: Lovelace}]
  expect: [{First: Ada, Last: Lovelace, Full: "Ada Lovelace"}]

- name: where_filters_out
  query: "source | where Price > 80"
  input:  [{Price: 10}]
  expect: []                                   # filtered → no rows

- name: unsupported_summarize
  query: "source | summarize count() by X"
  expect_error: KqlUnsupportedError            # compile-time rejection
```
Keys: `name`, `query`, `input`, `expect` **or** `expect_error`; optional
`schema`, `options`, `skip`. Records compare KQL-aware (float tolerance;
column-order-insensitive).

### 7.2 Runner
`tests/test_cases.py` discovers every YAML case and parametrizes one test each
(dropped files auto-run), exercising the public API. Rich diff on failure.

### 7.3 Categories & coverage
`cases/operators/`, `cases/functions/`, `cases/operators_scalar/`,
`cases/semantics/`, `cases/dcr/` (verbatim doc examples), `cases/asim/`
(normalization snippets — robustness benchmark), `cases/regression/`. CI gates
that every supported item (Appendix A) has ≥1 case.

### 7.4 Conformance oracle (dev-only, optional)
Generate/verify golden `expect` by running the same `query`+`input` through a
reference (Rx.KQL via a throwaway .NET runner, or ADX/Kusto emulator). Maintainer
tool only — not a runtime dependency.

### 7.5 Property/fuzz tests
`hypothesis`: parser round-trips; evaluation never raises in non-strict mode.

---

## 8. Repository & packaging

```
streaming_kql/           # importable package (py.typed)
  __init__.py api.py errors.py nodes.py parser.py evaluator.py functions.py
docs/  SPEC.md  (supported-kql.md, examples.md — later)
tests/  test_cases.py  cases/**/*.yaml
pyproject.toml README.md LICENSE NOTICE CHANGELOG.md CONTRIBUTING.md
.github/workflows/ci.yml  .gitignore
```

- **License:** Apache-2.0 + `NOTICE` (attributes Rx.KQL; KQL semantics per MS docs).
- **Build:** `pyproject.toml` (hatchling); Python **3.10+**.
- **Quality:** `ruff`, `mypy` (strict on core), `pytest`+`pytest-cov`,
  `hypothesis`.
- **CI:** GitHub Actions matrix (3.10–3.13; Linux/Windows/macOS): lint, type,
  test, coverage + Appendix-A coverage gate.
- **Release:** semver; `CHANGELOG.md`; PyPI via GitHub trusted publishing (OIDC).

---

## 9. Milestones

- **M0 — Skeleton** *(done)*: repo, packaging, CI, data-driven runner; **Lark**
  parser; working `where`/`extend`/`project`/`project-away`/`project-rename` +
  scalar grammar + a growing scalar-function set; stateful operators rejected.
- **M1 — Core stateless + scalars**: complete scalar/string/numeric operators and
  the most-used functions; broad case coverage.
- **M2 — DCR baseline complete**: `parse`/`parse-where` (regex full-match),
  `print`, `datatable`, `columnifexists`, `let`, dynamic handling, and the full
  DCR scalar-function list; `cases/dcr/` green → first PyPI release `0.1`.
- **M3 — Remaining stateless**: `project-keep`/`-reorder`, evaluate the deferred
  1→N operators individually (`mv-expand`, …), `cases/asim/` → `0.2`. Consider
  the Lark migration (§6.1) here.
- **M4 (future) — stateful extension** (opt-in): windowed `summarize`, bounded
  `sort`/`top`, etc.

---

## 10. Open questions

1. **Parser choice** (§6.1) — **resolved: Lark** (declarative grammar; pure-
   Python). Revisit only if ANTLR-based broad coverage becomes cheaper.
2. **Type strictness default** — null-tolerant (recommended) vs. strict.
3. **`int` vs `long`** — collapse to Python `int` (recommended) or model 32-bit.
4. **`decimal`** — include when, or defer.
5. **Deferred 1→N operators** — which of `mv-expand`/`bag_unpack`/`take`/`sample`
   have clean stateless semantics worth adding in M3.
6. **`parse_cef_dictionary`** — core (opt-in) vs. `extras`.

---

## Appendix A — supported-feature checklist (tracker)

> CI asserts every ✅ has ≥1 test. Legend: ☐ planned · ◐ partial · ✅ done.

**Statements:** ✅ `source` · ✅ `print` · ✅ `let`

**Tabular (DCR baseline):** ✅ `where` · ✅ `extend` · ✅ `project` · ✅
`project-away` · ✅ `project-rename` · ✅ `parse` · ✅ `parse-where` · ✅
`columnifexists` · ☐ `print` · ☐ `datatable`

**Tabular (stateless ext):** ✅ `project-keep` · ✅ `project-reorder` · ✅
`parse-kv` (beyond DCR)

**Tabular (deferred, per-operator):** ☐ `mv-expand` · ☐ `bag_unpack` · ☐
`take`/`limit` · ☐ `sample`

**Scalar operators:** ◐ numerical · ☐ datetime/timespan arithmetic · ◐ string
(`==`,`!=`,`=~`,`!~`,`contains(_cs)`,`has(_cs)`,`startswith(_cs)`,`endswith(_cs)`,
`matches regex`,`in`,`!in`) · ☐ `has_any`/`has_all` · ☐ bitwise

**Conversion:** ◐ `tobool` `todatetime` `todouble`/`toreal` `toint` `tolong`
`tostring` · ☐ `toguid` `totimespan`

**DateTime/TimeSpan:** ◐ `now` `ago` · ☐ (rest)

**Dynamic/array:** ◐ `parse_json` `array_length` · ☐ `array_concat` `pack`
`pack_array` `parse_xml` `zip`

**Math:** ✅ `abs` `bin`/`floor` `ceiling` `exp` `exp2` `exp10` `isfinite` `isinf`
`isnan` `log` `log2` `log10` `pow` `round` `sign`

**Conditional:** ✅ `iif`/`iff` `case` `coalesce` `max_of` `min_of`

**String:** ✅ `strcat` `strcat_delim` `strlen` `substring` `split` `replace`
`replace_regex` `extract` `extract_all` `indexof` `countof` `isempty` `isnotempty`
`tolower` `toupper` `base64_encodestring` `base64_decodestring` `hash_sha256`

**Type:** ◐ `gettype` `isnull` `isnotnull`

**Bitwise:** ☐ `binary_and/or/not/xor/shift_left/shift_right`

**Special:** ☐ `parse_cef_dictionary` · ✗ `geo_location` (not planned)

---

## Appendix B — sources

- Azure Monitor transformations — supported KQL: <https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql>
- KQL reference: <https://learn.microsoft.com/en-us/kusto/query/>
- ASIM parsers: <https://learn.microsoft.com/en-us/azure/sentinel/normalization-about-parsers>
- Rx.KQL (Apache-2.0 reference): <https://github.com/microsoft/RxKql>
- Lark: <https://github.com/lark-parser/lark> · ANTLR4 Python target: <https://github.com/antlr/antlr4/blob/master/doc/python-target.md>
