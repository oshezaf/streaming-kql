# Changelog

All notable changes to **streaming-kql** are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Timespan literals** (`1d`, `2h`, `30m`, `500ms`, `microseconds`, `ticks`)
  compiled to `datetime.timedelta`, enabling datetime/timespan arithmetic such as
  `now() - Timestamp`, `Timestamp + 30m`, and `where Created > ago(1d)`.
- **`parse_xml`** scalar function — parses an XML string into a `dynamic` object
  (attributes as `@name`, repeated children as lists, text as scalar/`#text`).
- **`evaluate bag_unpack` operator** (1→1) — expands a `dynamic` bag column into
  columns, with an optional name prefix.
- **Large stateless scalar-function batch** toward `0.1.0`:
  - **IP:** `parse_ipv4`, `ipv4_is_in_range`, `ipv4_is_private`, `ipv4_compare`,
    `ipv4_netmask_suffix`, `format_ipv4`.
  - **URL:** `parse_url`, `parse_urlquery`, `url_encode`, `url_encode_component`,
    `url_decode`.
  - **Bitwise:** `binary_and/or/xor/not/shift_left/shift_right`.
  - **Conversion/type:** `toguid`, `todecimal`, `isascii`.
  - **Hash:** `hash_md5`, `hash_sha1`.
  - **Math:** `sin`/`cos`/`tan`/`asin`/`acos`/`atan`/`atan2`, `degrees`/`radians`,
    `gamma`/`log_gamma`, `gcd`/`lcm`.
  - **String:** `strcmp`, `translate`, `indexof_regex`, `parse_csv`.
  - **DateTime:** `make_datetime`, `make_timespan`, `datetime_add`,
    `datetime_part`, `endofday`/`endofmonth`/`endofyear`, `weekofyear`,
    `unixtime_seconds`/`milliseconds`/`microseconds`_todatetime.
  - **Dynamic/array:** `bag_merge`, `bag_remove_keys`, `array_sort_asc`/`desc`,
    `array_reverse`, `array_sum`, `array_rotate_left`/`right`, `array_split`.
  - **Transformation-only:** `parse_cef_dictionary`.
  Data-driven cases added for the batch.

### Added (earlier)
- **Operators:** `let` (scalar bindings), `print` (row generator source),
  `project-reorder`, and **`parse-kv`** (key/value extraction, *beyond* the DCR
  baseline — [docs](https://learn.microsoft.com/en-us/kusto/query/parse-kv-operator)),
  with `pair_delimiter`/`kv_delimiter`/`quote` options.
- **More stateless functions:** `trim`/`trim_start`/`trim_end`, `strcat_array`,
  `reverse`, `sqrt`, `tohex`, `array_index_of`, `array_slice`, `bag_keys`,
  `set_union`/`set_intersect`/`set_difference`, `format_datetime`,
  `datetime_diff`, `dayofweek`.
- **Spec §5.7 survey** of further stateless-compatible operators and functions
  (IP/URL/dynamic/datetime/hash families) to guide upcoming milestones.

### Added (M2) `parse` and `parse-where` operators (`simple` + `regex`
  kinds; regex uses named groups with a greedy final column and DCR-style
  matching), `project-keep`, and the `columnifexists` function. New math/
  encoding/datetime functions (`abs`/`ceiling`/`floor`/`bin`/`round`/`sign`/
  `pow`/`exp*`/`log*`/`isnan`/`isinf`/`isfinite`, `hash_sha256`,
  `base64_encodestring`/`base64_decodestring`, `extract_all`, `array_concat`/
  `pack`/`pack_array`, `getyear`/`getmonth`/`dayofmonth`/`dayofyear`/`hourofday`/
  `startofday`/`startofmonth`/`startofyear`, `totimespan`). Data-driven cases
  added for all of the above.
- **Release workflow** (`.github/workflows/release.yml`): build sdist+wheel and
  publish to PyPI via **trusted publishing (OIDC)** on `v*` tags.

### Fixed
- **Docs:** reclassified `parse-where` from the DCR baseline to *stateless
  extensions (beyond DCR)*. The official Azure Monitor transformations operator
  list (`extend`, `project`, `print`, `where`, `parse`, `project-away`,
  `project-rename`, `datatable`, `columnifexists`) does **not** include
  `parse-where`; it remains fully supported here as an extension.

### Project scaffold (M0/M1)
- **Parser: Lark grammar + Transformer** (declarative, pure-Python) lowering to a
  parser-agnostic AST — chosen over a hand-written parser to scale toward the
  full DCR surface and a future stateful extension.
- **Engine:** a per-record evaluator for the streaming stateless subset:
  operators `where`/`filter`, `extend`, `project`, `project-away`,
  `project-rename`; a scalar-expression grammar (literals, columns, member/index
  access, arithmetic, comparisons, string operators `has`/`contains`/
  `startswith`/`endswith` and their `_cs`/negated forms, `in`/`!in`,
  `matches regex`, `and`/`or`/`not`, function calls).
- **Scalar-function library:** string (`strcat`/`strcat_delim`/`substring`/
  `split`/`replace`/`replace_regex`/`extract`/`extract_all`/`indexof`/`countof`/
  `strlen`/`tolower`/`toupper`/`isempty`/`isnotempty`), conversion (`tostring`/
  `toint`/`tolong`/`toreal`/`todouble`/`tobool`/`todatetime`), conditional
  (`iif`/`iff`/`case`/`coalesce`/`max_of`/`min_of`), type (`gettype`/`isnull`/
  `isnotnull`), math (`abs`/`ceiling`/`floor`/`bin`/`round`/`sign`/`pow`/`exp`/
  `exp2`/`exp10`/`log`/`log2`/`log10`/`isnan`/`isinf`/`isfinite`), hashing/
  encoding (`hash_sha256`/`base64_encodestring`/`base64_decodestring`), dynamic
  (`parse_json`/`array_length`/`array_concat`/`pack`/`pack_array`), datetime
  parts (`getyear`/`getmonth`/`dayofmonth`/`hourofday`/`startofday`/`now`/`ago`).
- **API:** `compile()`, `Query.transform/match/stream`, `Node` multi-query host,
  `Schema`, `Options`, `@function` custom-function registration, typed errors.
- **Stateful operators rejected at compile time** with `KqlUnsupportedError`
  (`summarize`, `join`, `sort`, `top`, `union`, …); `mv-expand`/`bag_unpack`/
  `take`/`sample` deferred pending per-operator evaluation.
- **Data-driven test suite:** YAML cases auto-discovered by `tests/test_cases.py`
  (`operators/`, `functions/`, `semantics/`, `dcr/` — the last adapted from the
  Azure Monitor transformation docs).
- Tooling: `pyproject.toml` (hatchling), `ruff`, `mypy`, `pytest`, GitHub Actions
  CI, `docs/SPEC.md` specification.
