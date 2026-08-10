# Contributing to streaming-kql

Thanks for helping build a pure-Python streaming KQL engine!

## Adding test coverage (no Python required)

The conformance suite is **data-driven**. To add a case, drop a YAML file under
`tests/cases/<category>/` with one or more cases:

```yaml
- name: extend_strcat
  query: |
    source | extend Full = strcat(First, " ", Last)
  schema: {First: string, Last: string}     # optional
  options: {now: "2026-01-01T00:00:00Z"}     # optional
  input:
    - {First: Ada, Last: Lovelace}
  expect:
    - {First: Ada, Last: Lovelace, Full: "Ada Lovelace"}
```

Keys: `name`, `query`, `input` (list of records), and either `expect` (list of
records) or `expect_error` (an exception class name, e.g. `KqlUnsupportedError`).
Optional: `schema`, `options`, `skip`. Cases are discovered automatically:

```bash
pytest
```

Every operator/function added to the supported set **must** ship at least one
case (see the tracker in `docs/SPEC.md` Appendix A).

## Development

```bash
python -m venv .venv && . .venv/Scripts/activate   # (Windows)
pip install -e ".[dev]"
ruff check . && mypy && pytest
```

## Scope

The engine is **stateless / per-record**. Operators that act across records or
reorder rows (`summarize`, `join`, `sort`, `top`, …) are out of scope for the
core and are rejected at compile time. A future stateful extension will live
behind an explicit opt-in — please open an issue to discuss before adding one.

By contributing you agree your contributions are licensed under Apache-2.0.
