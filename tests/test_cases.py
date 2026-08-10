"""Data-driven conformance runner.

Every ``*.yaml`` file under ``tests/cases/`` is discovered automatically and each
case becomes one parametrized test. A contributor adds coverage by dropping a
YAML file — no Python required. See docs/SPEC.md §7 for the case format.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

import streaming_kql as kql
from streaming_kql import Options, Schema

CASES_DIR = Path(__file__).parent / "cases"


def _load_cases() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(CASES_DIR.rglob("*.yaml")):
        docs = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(docs, dict):
            docs = [docs]
        for case in docs:
            cid = f"{path.relative_to(CASES_DIR).as_posix()}::{case.get('name', '?')}"
            out.append((cid, case))
    return out


_ALL = _load_cases()


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        return ("~", round(v, 9))
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _records_equal(a: list[dict], b: list[dict]) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b, strict=False):
        if {k: _norm(v) for k, v in ra.items()} != {k: _norm(v) for k, v in rb.items()}:
            # allow float tolerance
            if set(ra) != set(rb):
                return False
            for k in ra:
                va, vb = ra[k], rb[k]
                if isinstance(va, float) or isinstance(vb, float):
                    if va is None or vb is None or not math.isclose(
                            float(va), float(vb), rel_tol=1e-9, abs_tol=1e-9):
                        return False
                elif _norm(va) != _norm(vb):
                    return False
    return True


@pytest.mark.parametrize("cid,case", _ALL, ids=[c for c, _ in _ALL])
def test_case(cid: str, case: dict[str, Any]) -> None:
    if case.get("skip"):
        pytest.skip(case.get("skip"))
    query_text = case["query"]
    schema = Schema(case["schema"]) if case.get("schema") else None
    opts = None
    if case.get("options"):
        o = case["options"]
        now = None
        if o.get("now"):
            from datetime import datetime, timezone
            now = datetime.fromisoformat(str(o["now"]).replace("Z", "+00:00"))
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
        opts = Options(now=now, strict_types=bool(o.get("strict_types", False)))

    expect_error = case.get("expect_error")
    if expect_error:
        with pytest.raises(getattr(kql, expect_error)):
            q = kql.compile(query_text, schema=schema, options=opts)
            for rec in case.get("input", []):
                q.transform(rec)
        return

    q = kql.compile(query_text, schema=schema, options=opts)
    actual: list[dict] = []
    for rec in case.get("input", []):
        actual.extend(q.transform(rec))

    expected = case.get("expect", [])
    assert _records_equal(actual, expected), (
        f"\ncase: {cid}\nquery: {query_text}\nexpected: {expected}\nactual:   {actual}")
