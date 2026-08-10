"""Evaluator: compile an AST into per-record callables and run them.

Each tabular operator becomes ``Callable[[record], Iterable[record]]``; a query
is their left-to-right composition. Scalar expressions compile to
``Callable[[env], value]`` where ``env`` is the current record. This keeps
scalar evaluation independent of the (currently stateless) operator layer, so a
future stateful operator layer can reuse the same scalar engine.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from . import functions as fns
from .errors import KqlCompileError, KqlEvalError
from .nodes import (
    Binary,
    Call,
    Column,
    Expr,
    ExprList,
    Extend,
    Index,
    Literal,
    Member,
    Operator,
    Project,
    ProjectAway,
    ProjectRename,
    Query,
    Unary,
    Where,
)

Record = dict[str, Any]
_MISSING = object()


class Options:
    """Evaluation options (see docs/SPEC.md §4.4)."""

    def __init__(
        self,
        now: datetime | None = None,
        strict_types: bool = False,
    ):
        self.now = now
        self.strict_types = strict_types

    def clock(self) -> datetime:
        return self.now or datetime.now(timezone.utc)


# --- scalar compilation ------------------------------------------------------
def _truthy(v: Any) -> bool:
    return v is True


def _has_word(hay: str, needle: str, cs: bool) -> bool:
    if not needle:
        return False
    flags = 0 if cs else re.IGNORECASE
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])",
                     hay, flags) is not None


def _cmp(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        if op == "==":
            return a is None and b is None
        if op == "!=":
            return not (a is None and b is None)
        return None
    try:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
    except TypeError:
        return None
    return None


def _arith(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    try:
        if op == "+":
            if isinstance(a, str) or isinstance(b, str):
                return fns._s(a) + fns._s(b)
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return None if b == 0 else a / b
        if op == "%":
            return None if b == 0 else a % b
    except TypeError:
        return None
    return None


def compile_expr(node: Expr, opts: Options) -> Callable[[Record], Any]:
    if isinstance(node, Literal):
        val = node.value
        return lambda env: val
    if isinstance(node, Column):
        name = node.name
        return lambda env: env.get(name)
    if isinstance(node, ExprList):
        parts = [compile_expr(i, opts) for i in node.items]
        return lambda env: [p(env) for p in parts]
    if isinstance(node, Member):
        target = compile_expr(node.target, opts)
        key = node.name

        def _member(env: Record) -> Any:
            t = target(env)
            return t.get(key) if isinstance(t, dict) else None

        return _member
    if isinstance(node, Index):
        target = compile_expr(node.target, opts)
        key_fn = compile_expr(node.key, opts)

        def _index(env: Record) -> Any:
            t, k = target(env), key_fn(env)
            if isinstance(t, dict):
                return t.get(fns._s(k) if not isinstance(k, str) else k)
            if isinstance(t, list) and isinstance(k, int):
                return t[k] if -len(t) <= k < len(t) else None
            return None

        return _index
    if isinstance(node, Unary):
        operand = compile_expr(node.operand, opts)
        if node.op == "not":
            return lambda env: (None if operand(env) is None else not _truthy(operand(env)))
        if node.op == "-":
            def _neg(env: Record) -> Any:
                v = operand(env)
                return None if v is None else -v
            return _neg
    if isinstance(node, Binary):
        return _compile_binary(node, opts)
    if isinstance(node, Call):
        return _compile_call(node, opts)
    raise KqlCompileError(f"cannot evaluate expression node {type(node).__name__}")


def _compile_binary(node: Binary, opts: Options) -> Callable[[Record], Any]:
    op = node.op
    left = compile_expr(node.left, opts)
    right = compile_expr(node.right, opts)

    if op == "and":
        return lambda env: _and(left(env), right(env))
    if op == "or":
        return lambda env: _or(left(env), right(env))
    if op in ("==", "!=", "<", "<=", ">", ">="):
        return lambda env: _cmp(op, left(env), right(env))
    if op in ("+", "-", "*", "/", "%"):
        return lambda env: _arith(op, left(env), right(env))
    if op == "=~":
        return lambda env: fns._s(left(env)).lower() == fns._s(right(env)).lower()
    if op == "!~":
        return lambda env: fns._s(left(env)).lower() != fns._s(right(env)).lower()
    if op in ("contains", "contains_cs", "!contains", "!contains_cs"):
        cs = op.endswith("_cs")
        neg = op.startswith("!")

        def _contains(env: Record) -> Any:
            h, n = fns._s(left(env)), fns._s(right(env))
            r = (n in h) if cs else (n.lower() in h.lower())
            return (not r) if neg else r
        return _contains
    if op in ("has", "has_cs", "!has", "!has_cs"):
        cs = op.endswith("_cs")
        neg = op.startswith("!")
        return lambda env: (lambda r: (not r) if neg else r)(
            _has_word(fns._s(left(env)), fns._s(right(env)), cs))
    if op in ("startswith", "startswith_cs", "!startswith", "!startswith_cs"):
        cs = op.endswith("_cs")
        neg = op.startswith("!")

        def _sw(env: Record) -> Any:
            h, n = fns._s(left(env)), fns._s(right(env))
            r = h.startswith(n) if cs else h.lower().startswith(n.lower())
            return (not r) if neg else r
        return _sw
    if op in ("endswith", "endswith_cs", "!endswith", "!endswith_cs"):
        cs = op.endswith("_cs")
        neg = op.startswith("!")

        def _ew(env: Record) -> Any:
            h, n = fns._s(left(env)), fns._s(right(env))
            r = h.endswith(n) if cs else h.lower().endswith(n.lower())
            return (not r) if neg else r
        return _ew
    if op in ("in", "!in"):
        neg = op.startswith("!")

        def _in(env: Record) -> Any:
            needle = left(env)
            hay = right(env)
            if not isinstance(hay, list):
                hay = [hay]
            r = needle in hay
            return (not r) if neg else r
        return _in
    if op == "matches regex":
        def _matches(env: Record) -> Any:
            try:
                return re.search(fns._s(right(env)), fns._s(left(env))) is not None
            except re.error:
                return None
        return _matches
    raise KqlCompileError(f"unknown operator '{op}'")


def _and(a: Any, b: Any) -> Any:
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return a is True and b is True


def _or(a: Any, b: Any) -> Any:
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return a is True or b is True


def _parse_timespan_literal(_: Any) -> Any:  # placeholder for future literal support
    return None


def _compile_call(node: Call, opts: Options) -> Callable[[Record], Any]:
    name = node.name
    argfns = [compile_expr(a, opts) for a in node.args]

    if name == "now":
        return lambda env: opts.clock()
    if name == "ago":
        def _ago(env: Record) -> Any:
            v = argfns[0](env) if argfns else None
            if isinstance(v, timedelta):
                return opts.clock() - v
            return None
        return _ago

    fn = fns.get(name)
    if fn is None:
        raise KqlCompileError(f"unknown function '{name}'")

    def _call(env: Record) -> Any:
        args = [f(env) for f in argfns]
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            if opts.strict_types:
                raise KqlEvalError(f"{name}() failed: {e}") from e
            return None

    return _call


# --- operator compilation ----------------------------------------------------
OpFn = Callable[[Record], Iterable[Record]]


def _compile_operator(op: Operator, opts: Options) -> OpFn:
    if isinstance(op, Where):
        pred = compile_expr(op.predicate, opts)
        return lambda rec: (rec,) if _truthy(pred(rec)) else ()
    if isinstance(op, Extend):
        assigns = [(name, compile_expr(expr, opts)) for name, expr in op.assignments]

        def _extend(rec: Record) -> Iterable[Record]:
            out = dict(rec)
            for name, f in assigns:
                out[name] = f(out)
            return (out,)
        return _extend
    if isinstance(op, Project):
        items = [(it.name, compile_expr(it.expr, opts) if it.expr is not None else None)
                 for it in op.items]

        def _project(rec: Record) -> Iterable[Record]:
            out: Record = {}
            for name, f in items:
                out[name] = rec.get(name) if f is None else f(rec)
            return (out,)
        return _project
    if isinstance(op, ProjectAway):
        drop = set(op.names)
        return lambda rec: ({k: v for k, v in rec.items() if k not in drop},)
    if isinstance(op, ProjectRename):
        pairs = op.pairs

        def _rename(rec: Record) -> Iterable[Record]:
            out = dict(rec)
            for new, old in pairs:
                if old in out:
                    out[new] = out.pop(old)
            return (out,)
        return _rename
    raise KqlCompileError(f"cannot compile operator {type(op).__name__}")


class CompiledQuery:
    """A compiled, reusable query (see ``streaming_kql.compile``)."""

    def __init__(self, query: Query, opts: Options):
        self._opts = opts
        self._ops = [_compile_operator(op, opts) for op in query.operators]

    def transform(self, record: Record) -> list[Record]:
        """One record in → 0..N records out."""
        current: list[Record] = [dict(record)]
        for op in self._ops:
            nxt: list[Record] = []
            for rec in current:
                nxt.extend(op(rec))
            current = nxt
            if not current:
                break
        return current

    def match(self, record: Record) -> Record | None:
        """Convenience for 1→≤1 queries. Raises if more than one row is emitted."""
        out = self.transform(record)
        if len(out) > 1:
            raise KqlEvalError("query emitted multiple rows; use transform()/stream()")
        return out[0] if out else None

    def stream(self, records: Iterable[Record]) -> Iterator[Record]:
        for rec in records:
            yield from self.transform(rec)
