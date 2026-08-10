# Changelog

All notable changes to **streaming-kql** are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **M2 progress:** `parse` and `parse-where` operators (`simple` + `regex`
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
