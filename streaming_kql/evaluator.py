"""Evaluator: compile an AST into per-record callables and run them.

Each tabular operator becomes ``Callable[[record], Iterable[record]]``; a query
is their left-to-right composition. Scalar expressions compile to
``Callable[[env], value]`` where ``env`` is the current record. This keeps
scalar evaluation independent of the (currently stateless) operator layer, so a
future stateful operator layer can reuse the same scalar engine.
"""
from __future__ import annotations

import csv
import decimal as _decimal
import io
import json
import random
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import functions as fns
from .errors import KqlCompileError, KqlEvalError, KqlUnsupportedError
from .nodes import (
    As,
    BagUnpack,
    Binary,
    Call,
    Column,
    Count,
    Datatable,
    Distinct,
    Expr,
    ExprList,
    Extend,
    ExternalData,
    Fork,
    GetSchema,
    Index,
    Join,
    Literal,
    Lookup,
    MakeSeries,
    Member,
    MvApply,
    MvExpand,
    Operator,
    Parse,
    ParseKv,
    Partition,
    Project,
    ProjectAway,
    ProjectKeep,
    ProjectRename,
    ProjectReorder,
    Query,
    Range,
    Sample,
    SampleDistinct,
    Serialize,
    Sort,
    Summarize,
    Take,
    Top,
    Unary,
    Union,
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
        random_seed: int | None = None,
    ):
        self.now = now
        self.strict_types = strict_types
        self.random_seed = random_seed
        self._lets: dict[str, Any] = {}
        self._tables: dict[str, list[Record]] = {}

    def clock(self) -> datetime:
        return self.now or datetime.now(timezone.utc)

    def rng(self) -> random.Random:
        """A random generator, seeded (deterministic) when ``random_seed`` is set."""
        return random.Random(self.random_seed)


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
    except (TypeError, ZeroDivisionError):
        return None
    return None


def compile_expr(node: Expr, opts: Options) -> Callable[[Record], Any]:
    if isinstance(node, Literal):
        val = node.value
        return lambda env: val
    if isinstance(node, Column):
        name = node.name
        return lambda env: env[name] if name in env else opts._lets.get(name)
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
    if op in ("has_any", "has_all"):
        require_all = op == "has_all"

        def _has_multi(env: Record) -> Any:
            hay = fns._s(left(env))
            terms = right(env)
            if not isinstance(terms, list):
                terms = [terms]
            checks = [
                _has_word(hay, fns._s(t), False) for t in terms if t is not None
            ]
            if not checks:
                return False
            return all(checks) if require_all else any(checks)
        return _has_multi
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
    if name == "columnifexists":
        col_fn = argfns[0] if argfns else (lambda e: None)
        default_fn = argfns[1] if len(argfns) > 1 else (lambda e: None)

        def _cie(env: Record) -> Any:
            cn = col_fn(env)
            if isinstance(cn, str) and cn in env:
                return env[cn]
            return default_fn(env)
        return _cie

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


# --- parse operator ----------------------------------------------------------
def _cast(raw: Any, col_type: str | None) -> Any:
    if raw is None:
        return None
    if col_type in (None, "string", "guid"):
        return raw
    if col_type in ("long", "int"):
        return fns._toint(raw)
    if col_type in ("real", "double", "decimal"):
        return fns._toreal(raw)
    if col_type == "datetime":
        return fns._todatetime(raw)
    if col_type in ("bool", "boolean"):
        return fns._tobool(raw)
    return raw


# --- type coercion (schema / datatable / externaldata) -----------------------
def _coerce(value: Any, col_type: str | None) -> Any:
    """Coerce *value* to the declared KQL *col_type* (see docs/SPEC.md §3.2).

    A KQL null (Python ``None``) stays null. A value that cannot be converted
    becomes null (KQL's null-tolerant behavior); callers using ``strict_types``
    surface the failure elsewhere. Values already of the target type pass
    through unchanged.
    """
    if value is None:
        return None
    t = (col_type or "").lower()
    if t in ("", "string"):
        return value if isinstance(value, str) else fns._s(value)
    if t in ("int", "long"):
        return fns._toint(value)
    if t in ("real", "double"):
        return fns._toreal(value)
    if t == "decimal":
        return fns._todecimal(value)
    if t == "datetime":
        return fns._todatetime(value)
    if t == "timespan":
        return fns._totimespan(value)
    if t in ("bool", "boolean"):
        return fns._tobool(value)
    if t == "guid":
        return fns._toguid(value)
    if t == "dynamic":
        return fns._parse_json(value)
    return value


def _hkey(v: Any) -> Any:
    """A hashable join-key representation of *v*."""
    try:
        hash(v)
        return v
    except TypeError:
        return fns._s(v)



def _simple_match(segments: tuple, text: str) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    pos = 0
    pending = None  # a 'col'/'star' ParseSeg awaiting its end boundary
    cap_start = 0
    for seg in segments:
        if seg.kind == "lit":
            idx = text.find(seg.value, pos)
            if idx == -1:
                return None
            if pending is not None:
                if pending.kind == "col":
                    result[pending.value] = _cast(text[cap_start:idx], pending.col_type)
                pending = None
            pos = idx + len(seg.value)
        else:  # col or star
            if pending is not None and pending.kind == "col":
                result[pending.value] = _cast("", pending.col_type)
            pending = seg
            cap_start = pos
    if pending is not None and pending.kind == "col":
        result[pending.value] = _cast(text[cap_start:], pending.col_type)
    return result


def _build_regex_matcher(segments: tuple) -> Callable[[str], dict[str, Any] | None]:
    last_col_idx = max((i for i, s in enumerate(segments) if s.kind == "col"),
                       default=-1)
    parts: list[str] = []
    types: dict[str, str | None] = {}
    for i, seg in enumerate(segments):
        if seg.kind == "lit":
            parts.append(seg.value)
        elif seg.kind == "star":
            parts.append("(?:.*?)")
        else:
            greedy = ".*" if i == last_col_idx else ".*?"
            parts.append(f"(?P<{seg.value}>{greedy})")
            types[seg.value] = seg.col_type
    pattern = "".join(parts)
    try:
        rx = re.compile(pattern, re.DOTALL)
    except re.error:
        rx = None

    def _match(text: str) -> dict[str, Any] | None:
        if rx is None:
            return None
        m = rx.search(text)
        if not m:
            return None
        return {name: _cast(m.group(name), types[name]) for name in types}

    return _match


def _compile_parse(op: Parse, opts: Options) -> OpFn:
    src = compile_expr(op.source, opts)
    col_segs = [s for s in op.segments if s.kind == "col"]
    if op.kind == "regex":
        matcher = _build_regex_matcher(op.segments)
    else:
        def matcher(text: str) -> dict[str, Any] | None:
            return _simple_match(op.segments, text)

    def _parse(rec: Record) -> Iterable[Record]:
        cols = matcher(fns._s(src(rec)))
        out = dict(rec)
        if cols is None:
            if op.drop_unmatched:
                return ()
            for s in col_segs:
                out[s.value] = None
            return (out,)
        out.update(cols)
        return (out,)

    return _parse


def _compile_parsekv(op: ParseKv, opts: Options) -> OpFn:
    src = compile_expr(op.source, opts)
    o = dict(op.options)
    pair_delim = o.get("pair_delimiter", " ")
    kv_delim = o.get("kv_delimiter", "=")
    quote = o.get("quote")
    cols = op.columns

    def _pkv(rec: Record) -> Iterable[Record]:
        text = fns._s(src(rec))
        kv: dict[str, str] = {}
        pairs = text.split(pair_delim) if pair_delim else [text]
        for pair in pairs:
            if kv_delim and kv_delim in pair:
                k, v = pair.split(kv_delim, 1)
                k = k.strip()
                v = v.strip()
                if quote:
                    v = v.strip(quote)
                if k:
                    kv[k] = v
        out = dict(rec)
        for name, col_type in cols:
            out[name] = _cast(kv[name], col_type) if name in kv else None
        return (out,)

    return _pkv


# --- constant reference tables (datatable / externaldata) --------------------
def _materialize_table(node: object, opts: Options) -> list[Record]:
    """Evaluate a ``datatable``/``externaldata`` producer into constant rows."""
    if isinstance(node, Datatable):
        cols = node.columns
        n = len(cols)
        if n == 0:
            return []
        vals = [compile_expr(v, opts)({}) for v in node.values]
        if len(vals) % n != 0:
            raise KqlCompileError(
                f"datatable has {len(vals)} values, which is not a multiple of its "
                f"{n} column(s)")
        rows: list[Record] = []
        for i in range(0, len(vals), n):
            chunk = vals[i:i + n]
            rows.append({cname: _coerce(raw, ctype)
                         for (cname, ctype), raw in zip(cols, chunk, strict=True)})
        return rows
    if isinstance(node, ExternalData):
        return _read_externaldata(node, opts)
    if isinstance(node, Range):
        return _materialize_range(node, opts)
    raise KqlCompileError(f"cannot materialize table producer {type(node).__name__}")


_RANGE_ROW_CAP = 10_000_000


def _materialize_range(node: Range, opts: Options) -> list[Record]:
    start = compile_expr(node.start, opts)({})
    stop = compile_expr(node.stop, opts)({})
    step = compile_expr(node.step, opts)({})
    if start is None or stop is None or step is None:
        raise KqlCompileError("range 'from'/'to'/'step' must be non-null constants")
    zero = start - start  # 0 or timedelta(0), matching the value type
    if step == zero:
        raise KqlCompileError("range 'step' must be non-zero")
    ascending = step > zero
    rows: list[Record] = []
    v = start
    while (v <= stop) if ascending else (v >= stop):
        rows.append({node.name: v})
        if len(rows) > _RANGE_ROW_CAP:
            raise KqlCompileError(
                f"range would generate more than {_RANGE_ROW_CAP} rows")
        v = v + step
    return rows


def _resolve_local_path(uri: str) -> Path:
    """Resolve a local file path or ``file://`` URI. Remote schemes are rejected
    because streaming-kql is an offline, in-process library."""
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    if scheme in ("", "file"):
        if scheme == "file":
            local = unquote(parsed.path)
            # Windows: file:///C:/dir/file -> C:/dir/file
            if re.match(r"/[A-Za-z]:", local):
                local = local[1:]
        else:
            local = uri
        path = Path(local)
        if not path.exists():
            raise KqlCompileError(f"externaldata file not found: {uri}")
        return path
    raise KqlUnsupportedError(
        f"externaldata scheme '{scheme}://' is not supported; streaming-kql is an "
        "offline library and reads only local files (a path or a file:// URI)")


def _parse_external(text: str, fmt: str,
                    cols: tuple[tuple[str, str], ...]) -> list[Record]:
    rows: list[Record] = []
    if fmt in ("csv", "tsv", "scsv", "psv", "txt", "raw"):
        if fmt in ("txt", "raw"):
            cname, ctype = cols[0]
            for line in text.splitlines():
                if line == "":
                    continue
                rows.append({cname: _coerce(line, ctype)})
            return rows
        delim = {"csv": ",", "tsv": "\t", "scsv": ";", "psv": "|"}[fmt]
        for fields in csv.reader(io.StringIO(text), delimiter=delim):
            if not fields or (len(fields) == 1 and fields[0] == ""):
                continue
            row: Record = {}
            for i, (cname, ctype) in enumerate(cols):
                raw = fields[i] if i < len(fields) else None
                row[cname] = _coerce(raw, ctype)
            rows.append(row)
        return rows
    if fmt == "json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        for obj in data:
            rows.append({cn: _coerce(obj.get(cn) if isinstance(obj, dict) else None, ct)
                         for cn, ct in cols})
        return rows
    if fmt in ("multijson", "jsonl", "json-lines"):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append({cn: _coerce(obj.get(cn) if isinstance(obj, dict) else None, ct)
                         for cn, ct in cols})
        return rows
    raise KqlUnsupportedError(f"externaldata format '{fmt}' is not supported")


def _read_externaldata(node: ExternalData, opts: Options) -> list[Record]:
    o = {k.lower(): v for k, v in node.options}
    fmt = (o.get("format") or "csv").lower()
    rows: list[Record] = []
    for uexpr in node.uris:
        uri = fns._s(compile_expr(uexpr, opts)({}))
        text = _resolve_local_path(uri).read_text(encoding="utf-8")
        rows.extend(_parse_external(text, fmt, node.columns))
    return rows


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
    if isinstance(op, ProjectKeep):
        keep = set(op.names)
        return lambda rec: ({k: v for k, v in rec.items() if k in keep},)
    if isinstance(op, ProjectReorder):
        order = op.names

        def _reorder(rec: Record) -> Iterable[Record]:
            out: Record = {n: rec[n] for n in order if n in rec}
            for k, v in rec.items():
                if k not in out:
                    out[k] = v
            return (out,)
        return _reorder
    if isinstance(op, Parse):
        return _compile_parse(op, opts)
    if isinstance(op, ParseKv):
        return _compile_parsekv(op, opts)
    if isinstance(op, BagUnpack):
        col = op.column
        prefix = op.prefix

        def _bag_unpack(rec: Record) -> Iterable[Record]:
            out = {k: v for k, v in rec.items() if k != col}
            bag = rec.get(col)
            if isinstance(bag, dict):
                for k, v in bag.items():
                    out[f"{prefix}{k}"] = v
            return (out,)
        return _bag_unpack
    if isinstance(op, ProjectRename):
        pairs = op.pairs

        def _rename(rec: Record) -> Iterable[Record]:
            out = dict(rec)
            for new, old in pairs:
                if old in out:
                    out[new] = out.pop(old)
            return (out,)
        return _rename
    if isinstance(op, Lookup):
        return _compile_lookup(op, opts)
    if isinstance(op, MvExpand):
        return _compile_mvexpand(op, opts)
    raise KqlCompileError(f"cannot compile operator {type(op).__name__}")


def _compile_lookup(op: Lookup, opts: Options) -> OpFn:
    table_rows = opts._tables.get(op.table)
    if table_rows is None:
        raise KqlCompileError(
            f"lookup references unknown table '{op.table}'; define it first with "
            "'let {name} = datatable(...)/externaldata(...);'".format(name=op.table))
    left_keys = [lk for lk, _ in op.keys]
    right_keys = [rk for _, rk in op.keys]
    right_key_set = set(right_keys)

    # Column order of the reference table (for null-filling on no match).
    right_columns: list[str] = []
    seen: set[str] = set()
    for r in table_rows:
        for c in r:
            if c not in seen:
                seen.add(c)
                right_columns.append(c)
    add_columns = [c for c in right_columns if c not in right_key_set]

    # Index the (constant) reference table by its join keys; first row wins.
    index: dict[tuple, Record] = {}
    for r in table_rows:
        k = tuple(_hkey(r.get(rk)) for rk in right_keys)
        index.setdefault(k, r)

    inner = op.kind == "inner"

    def _lookup(rec: Record) -> Iterable[Record]:
        k = tuple(_hkey(rec.get(lk)) for lk in left_keys)
        match = index.get(k)
        out = dict(rec)
        if match is None:
            if inner:
                return ()
            for c in add_columns:
                if c not in out:
                    out[c] = None
            return (out,)
        for c in add_columns:
            out[c] = match.get(c)
        return (out,)

    return _lookup


def _mv_elements(v: Any) -> list[Any]:
    """Expansion values for one mv-expand column (KQL default expansion)."""
    if isinstance(v, list):
        return list(v)
    if isinstance(v, dict):
        return [{k: val} for k, val in v.items()]   # bag expansion
    if v is None:
        return []
    return [v]


def _col_getter(name: str) -> Callable[[Record], Any]:
    def _get(rec: Record) -> Any:
        return rec.get(name)
    return _get


def _compile_mvexpand(op: MvExpand, opts: Options) -> OpFn:
    # (out_name, source_fn): source_fn(rec) yields the value to expand.
    specs: list[tuple[str, Callable[[Record], Any]]] = []
    for name, src in op.columns:
        specs.append((name, _col_getter(name) if src is None
                      else compile_expr(src, opts)))
    idx = op.item_index
    limit = op.limit

    def _mvexpand(rec: Record) -> Iterable[Record]:
        seqs = [_mv_elements(fn(rec)) for _, fn in specs]
        n = max((len(s) for s in seqs), default=0)
        if limit is not None:
            n = min(n, limit)
        if n == 0:
            return ()
        out: list[Record] = []
        for i in range(n):
            new = dict(rec)
            for (name, _), s in zip(specs, seqs, strict=True):
                new[name] = s[i] if i < len(s) else None
            if idx is not None:
                new[idx] = i
            out.append(new)
        return out

    return _mvexpand


class _Ctx:
    """Per-record execution context, created fresh for each ``transform`` call
    (thread-safe). ``origin`` is the coerced input record (for ``source``
    subqueries); ``named`` holds tables captured by ``as``/``fork``."""

    __slots__ = ("origin", "named")

    def __init__(self, origin: Record):
        self.origin = origin
        self.named: dict[str, list[Record]] = {}


def _declares(op: Operator) -> set[str]:
    if isinstance(op, As):
        return {op.name}
    if isinstance(op, Fork):
        return {name for name, _ in op.branches}
    return set()


def _compile_steps(operators: tuple[Operator, ...],
                   opts: Options) -> list[tuple[bool, Callable]]:
    """Compile a sequence of operators, tracking ``as``/``fork`` names declared so
    far so that ``join``/``union`` references can be validated at compile time."""
    declared: set[str] = set()
    steps: list[tuple[bool, Callable]] = []
    for op in operators:
        steps.append(_compile_step(op, opts, declared))
        declared |= _declares(op)
    return steps


def _run_steps(steps: list[tuple[bool, Callable]], current: list[Record],
               ctx: _Ctx) -> list[Record]:
    for is_batch, f in steps:
        if is_batch:
            current = f(current, ctx)
        else:
            nxt: list[Record] = []
            for r in current:
                nxt.extend(f(r))
            current = nxt
        if not current:
            break
    return current


def _compile_query_plan(
    query: Query, opts: Options,
    schema: tuple[tuple[str, str], ...] = (),
) -> Callable[[Record], list[Record]]:
    """Compile a (sub)query into ``record -> rows``.

    A ``source`` subquery is seeded with the incoming record (coerced by
    *schema* if given), a ``print``/table subquery with its constant rows. Each
    step is either a **row** operator (``record -> rows``, applied per row) or a
    **batch** operator (``rows, ctx -> rows``, applied to the whole per-record
    row-set, e.g. ``summarize``/``join``).
    """
    steps = _compile_steps(query.operators, opts)
    if query.source_kind == "print":
        prow: Record = {}
        for i, (pname, pexpr) in enumerate(query.print_items):
            prow[pname or f"print_{i}"] = compile_expr(pexpr, opts)({})

        def seed(rec: Record) -> list[Record]:
            return [dict(prow)]
    elif query.source_kind == "table":
        trows = _materialize_table(query.head_table, opts)

        def seed(rec: Record) -> list[Record]:
            return [dict(r) for r in trows]
    elif schema:
        def seed(rec: Record) -> list[Record]:
            r = dict(rec)
            for col, ctype in schema:
                if col in r:
                    r[col] = _coerce(r[col], ctype)
            return [r]
    else:
        def seed(rec: Record) -> list[Record]:
            return [dict(rec)]

    def run(rec: Record) -> list[Record]:
        current = seed(rec)
        ctx = _Ctx(current[0] if current else {})
        return _run_steps(steps, current, ctx)

    return run


def _resolve_table_operand(
    name: str, opts: Options, declared: set[str], op_name: str,
) -> Callable[[_Ctx], list[Record]]:
    """Resolve a bare table name used by ``union``/``join`` to a
    ``ctx -> rows`` function: a constant ``let`` table, or an ``as``/``fork``
    named table captured earlier in the pipeline (resolved per record)."""
    const = opts._tables.get(name)
    if const is not None:
        rows = list(const)
        return lambda ctx: [dict(r) for r in rows]
    if name in declared:
        return lambda ctx: [dict(r) for r in ctx.named.get(name, [])]
    raise KqlCompileError(
        f"{op_name} references unknown table '{name}'; use a 'let'-bound "
        "datatable/externaldata/range, a name introduced earlier by 'as'/'fork', "
        "or '(source | ...)' for a subquery")


def _subquery_branch(
    plan: Callable[[Record], list[Record]],
) -> Callable[[_Ctx], list[Record]]:
    def _emit(ctx: _Ctx) -> list[Record]:
        return plan(ctx.origin)
    return _emit


def _compile_union(op: Union, opts: Options, declared: set[str]) -> BatchFn:
    branches: list[Callable[[_Ctx], list[Record]]] = []
    for operand in op.operands:
        if isinstance(operand, str):
            branches.append(_resolve_table_operand(operand, opts, declared, "union"))
        elif isinstance(operand, Query):
            branches.append(_subquery_branch(_compile_query_plan(operand, opts)))
        else:  # pragma: no cover - parser only yields str or Query
            raise KqlCompileError("invalid union operand")
    inner = op.kind == "inner"

    def _union(rows: list[Record], ctx: _Ctx) -> list[Record]:
        out: list[Record] = [dict(r) for r in rows]     # the incoming (left) rows
        for branch in branches:
            out.extend(branch(ctx))
        if inner:                                        # keep columns common to all
            common: set[str] | None = None
            for r in out:
                common = set(r) if common is None else (common & set(r))
            keep = common or set()
            return [{k: v for k, v in r.items() if k in keep} for r in out]
        # outer (default): null-fill the union of columns, first-seen order
        allcols: list[str] = []
        seen: set[str] = set()
        for r in out:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    allcols.append(k)
        return [{k: r.get(k) for k in allcols} for r in out]

    return _union


# --- batch (per-record row-set) operators ------------------------------------
# summarize / sort / order by / top / distinct / take|limit / join / union / as /
# fork / partition operate on the whole set of rows produced from one input
# record. Stateless: never cross records. A batch fn receives ``(rows, ctx)``
# where ctx carries the original (coerced) input record and the ``as``/``fork``
# named tables for the current record.
BatchFn = Callable[[list[Record], _Ctx], list[Record]]

_AGG_NAMES = {
    "count", "countif", "sum", "sumif", "avg", "avgif", "min", "max",
    "dcount", "make_list", "make_set", "any", "take_any",
}


def _num_or_none(v: Any) -> float | int | None:
    if isinstance(v, bool) or v is None:
        return None if v is None else int(v)
    if isinstance(v, (int, float)):
        return v
    return None


def _agg_sum(vals: Iterable[Any]) -> Any:
    total: Any = None
    for v in vals:
        n = _num_or_none(v)
        if n is not None:
            total = n if total is None else total + n
    return total


def _agg_avg(vals: Iterable[Any]) -> Any:
    total: float = 0.0
    count = 0
    for v in vals:
        n = _num_or_none(v)
        if n is not None:
            total += n
            count += 1
    return None if count == 0 else total / count


def _agg_minmax(vals: Iterable[Any], want_max: bool) -> Any:
    best: Any = None
    best_key: Any = None
    for v in vals:
        if v is None:
            continue
        k = _sortkey(v)
        if best_key is None or (k > best_key if want_max else k < best_key):
            best, best_key = v, k
    return best


def _compile_aggregate(call: Expr, opts: Options) -> Callable[[list[Record]], Any]:
    if not isinstance(call, Call) or call.name not in _AGG_NAMES:
        got = call.name if isinstance(call, Call) else type(call).__name__
        raise KqlCompileError(
            f"summarize expects an aggregate function (count/sum/avg/min/max/"
            f"dcount/make_list/make_set/countif/...), got '{got}'")
    name = call.name
    args = call.args
    if name == "count":
        return lambda grp: len(grp)
    if name == "countif":
        pred = compile_expr(args[0], opts)
        return lambda grp: sum(1 for r in grp if _truthy(pred(r)))
    if name in ("sumif", "avgif"):
        val = compile_expr(args[0], opts)
        pred = compile_expr(args[1], opts)
        agg = _agg_sum if name == "sumif" else _agg_avg
        return lambda grp: agg(val(r) for r in grp if _truthy(pred(r)))
    val = compile_expr(args[0], opts)
    if name == "sum":
        return lambda grp: _agg_sum(val(r) for r in grp)
    if name == "avg":
        return lambda grp: _agg_avg(val(r) for r in grp)
    if name == "min":
        return lambda grp: _agg_minmax((val(r) for r in grp), want_max=False)
    if name == "max":
        return lambda grp: _agg_minmax((val(r) for r in grp), want_max=True)
    if name == "dcount":
        return lambda grp: len({_hkey(val(r)) for r in grp if val(r) is not None})
    if name == "make_list":
        return lambda grp: [v for r in grp if (v := val(r)) is not None]

    if name == "make_set":
        def _mkset(grp: list[Record]) -> list[Any]:
            out: list[Any] = []
            seen: set[Any] = set()
            for r in grp:
                v = val(r)
                if v is None:
                    continue
                k = _hkey(v)
                if k not in seen:
                    seen.add(k)
                    out.append(v)
            return out
        return _mkset

    # any / take_any
    def _any(grp: list[Record]) -> Any:
        result: Any = None
        seen = False
        for r in grp:
            v = val(r)
            if not seen:
                result, seen = v, True
            if v is not None:
                return v
        return result
    return _any


def _compile_summarize(op: Summarize, opts: Options) -> BatchFn:
    key_specs = [(name, compile_expr(expr, opts)) for name, expr in op.by_keys]
    agg_specs = [(name, _compile_aggregate(call, opts)) for name, call in op.aggregates]

    def _summarize(rows: list[Record], ctx: _Ctx) -> list[Record]:
        groups: dict[tuple, tuple[list[Any], list[Record]]] = {}
        order: list[tuple] = []
        for r in rows:
            kvals = [kf(r) for _, kf in key_specs]
            key = tuple(_hkey(v) for v in kvals)
            if key not in groups:
                groups[key] = (kvals, [])
                order.append(key)
            groups[key][1].append(r)
        out: list[Record] = []
        for key in order:
            kvals, grp = groups[key]
            row: Record = {}
            for (kname, _), v in zip(key_specs, kvals, strict=True):
                row[kname] = v
            for aname, afn in agg_specs:
                row[aname] = afn(grp)
            out.append(row)
        return out

    return _summarize


def _sortkey(v: Any) -> tuple:
    """A null-safe, type-bucketed sort key (nulls first under ascending)."""
    if v is None:
        return (0,)
    if isinstance(v, bool):
        return (1, 0, int(v))
    if isinstance(v, (int, float)):
        return (1, 0, v)
    if isinstance(v, str):
        return (1, 1, v)
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return (1, 2, dt.timestamp())
    if isinstance(v, timedelta):
        return (1, 3, v.total_seconds())
    return (1, 4, fns._s(v))


def _sortkey_fn(kf: Callable[[Record], Any]) -> Callable[[Record], tuple]:
    def _k(r: Record) -> tuple:
        return _sortkey(kf(r))
    return _k


def _sort_rows(rows: list[Record],
               keys: list[tuple[Callable[[Record], Any], bool]]) -> list[Record]:
    out = list(rows)
    for keyfn, desc in reversed(keys):        # stable multi-key sort
        out.sort(key=_sortkey_fn(keyfn), reverse=desc)
    return out


def _ordered_cols(rows: list[Record]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for c in r:
            if c not in seen:
                seen.add(c)
                cols.append(c)
    return cols


_JOIN_KIND_ALIASES = {
    "anti": "leftanti", "leftantisemi": "leftanti", "rightantisemi": "rightanti",
    "leftouterunique": "leftouter",
}
_JOIN_KINDS = {
    "inner", "innerunique", "leftouter", "rightouter", "fullouter",
    "leftanti", "rightanti", "leftsemi", "rightsemi",
}


def _do_join(left: list[Record], right: list[Record],
             left_keys: list[str], right_keys: list[str], kind: str) -> list[Record]:
    left_cols = _ordered_cols(left)
    right_cols = _ordered_cols(right)

    # Right column names, renamed on collision with a left column (KQL: `X1`).
    rename: dict[str, str] = {}
    used = set(left_cols)
    for c in right_cols:
        nc, i = c, 1
        while nc in used:
            nc, i = f"{c}{i}", i + 1
        rename[c] = nc
        used.add(nc)

    def lkey(r: Record) -> tuple:
        return tuple(_hkey(r.get(k)) for k in left_keys)

    def rkey(r: Record) -> tuple:
        return tuple(_hkey(r.get(k)) for k in right_keys)

    # Semi/anti: emit one side's rows, unchanged, no column merge.
    if kind in ("leftsemi", "leftanti"):
        rkeys = {rkey(r) for r in right}
        keep = kind == "leftsemi"
        return [dict(x) for x in left if (lkey(x) in rkeys) == keep]
    if kind in ("rightsemi", "rightanti"):
        lkeys = {lkey(x) for x in left}
        keep = kind == "rightsemi"
        return [dict(r) for r in right if (rkey(r) in lkeys) == keep]

    index: dict[tuple, list[Record]] = {}
    for r in right:
        index.setdefault(rkey(r), []).append(r)

    all_cols = left_cols + [rename[c] for c in right_cols]

    def merged(lrow: Record | None, rrow: Record | None) -> Record:
        row: Record = {c: None for c in all_cols}
        if lrow is not None:
            for c in left_cols:
                row[c] = lrow.get(c)
        if rrow is not None:
            for c in right_cols:
                row[rename[c]] = rrow.get(c)
        return row

    left_rows = left
    if kind == "innerunique":                 # dedup left on the join key
        seen: set[tuple] = set()
        left_rows = []
        for x in left:
            k = lkey(x)
            if k not in seen:
                seen.add(k)
                left_rows.append(x)

    out: list[Record] = []
    matched: set[tuple] = set()
    for x in left_rows:
        k = lkey(x)
        hits = index.get(k)
        if hits:
            matched.add(k)
            out.extend(merged(x, r) for r in hits)
        elif kind in ("leftouter", "fullouter"):
            out.append(merged(x, None))
    if kind in ("rightouter", "fullouter"):
        for r in right:
            if rkey(r) not in matched:
                out.append(merged(None, r))
    return out


def _compile_join(op: Join, opts: Options, declared: set[str]) -> BatchFn:
    kind = _JOIN_KIND_ALIASES.get(op.kind, op.kind)
    if kind not in _JOIN_KINDS:
        raise KqlCompileError(
            f"unsupported join kind '{op.kind}'; expected one of "
            + ", ".join(sorted(_JOIN_KINDS)))
    left_keys = [lk for lk, _ in op.keys]
    right_keys = [rk for _, rk in op.keys]

    if isinstance(op.right, str):
        right_of = _resolve_table_operand(op.right, opts, declared, "join")
    elif isinstance(op.right, Query):
        plan = _compile_query_plan(op.right, opts)

        def right_of(ctx: _Ctx) -> list[Record]:
            return plan(ctx.origin)
    else:  # pragma: no cover - parser only yields str or Query
        raise KqlCompileError("invalid join right operand")

    def _join(rows: list[Record], ctx: _Ctx) -> list[Record]:
        return _do_join(rows, right_of(ctx), left_keys, right_keys, kind)

    return _join


def _compile_subpipeline(
    operators: tuple[Operator, ...], opts: Options,
) -> Callable[[list[Record], _Ctx], list[Record]]:
    """Compile a sub-pipeline that runs on a given row-set (no source seeding),
    sharing the caller's ``ctx``. Used by ``fork`` branches and ``partition``."""
    steps = _compile_steps(operators, opts)

    def run(rows: list[Record], ctx: _Ctx) -> list[Record]:
        return _run_steps(steps, [dict(r) for r in rows], ctx)

    return run


def _compile_as(op: As) -> BatchFn:
    name = op.name

    def _as(rows: list[Record], ctx: _Ctx) -> list[Record]:
        ctx.named[name] = [dict(r) for r in rows]
        return rows

    return _as


def _compile_fork(op: Fork, opts: Options) -> BatchFn:
    branches = [(name, _compile_subpipeline(ops, opts)) for name, ops in op.branches]

    def _fork(rows: list[Record], ctx: _Ctx) -> list[Record]:
        for name, run in branches:
            ctx.named[name] = run(rows, ctx)
        return rows                              # pass the input through unchanged

    return _fork


def _compile_partition(op: Partition, opts: Options) -> BatchFn:
    col = op.key
    run = _compile_subpipeline(op.operators, opts)

    def _partition(rows: list[Record], ctx: _Ctx) -> list[Record]:
        groups: dict[tuple, list[Record]] = {}
        order: list[tuple] = []
        for r in rows:
            k = (_hkey(r.get(col)),)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(r)
        out: list[Record] = []
        for k in order:
            out.extend(run(groups[k], ctx))
        return out

    return _partition


def _kql_type_name(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "long"
    if isinstance(v, float):
        return "real"
    if isinstance(v, str):
        return "string"
    if isinstance(v, datetime):
        return "datetime"
    if isinstance(v, timedelta):
        return "timespan"
    if isinstance(v, (dict, list)):
        return "dynamic"
    if isinstance(v, _decimal.Decimal):
        return "decimal"
    return "string"


def _compile_getschema() -> BatchFn:
    def _getschema(rows: list[Record], ctx: _Ctx) -> list[Record]:
        out: list[Record] = []
        for i, col in enumerate(_ordered_cols(rows)):
            ktype = "string"
            for r in rows:
                v = r.get(col)
                if v is not None:
                    ktype = _kql_type_name(v)
                    break
            out.append({"ColumnName": col, "ColumnOrdinal": i, "ColumnType": ktype})
        return out

    return _getschema


def _compile_count() -> BatchFn:
    def _count(rows: list[Record], ctx: _Ctx) -> list[Record]:
        return [{"Count": len(rows)}]

    return _count


def _compile_sample(op: Sample, opts: Options) -> BatchFn:
    n = op.count

    def _sample(rows: list[Record], ctx: _Ctx) -> list[Record]:
        if n >= len(rows):
            return [dict(r) for r in rows]
        idxs = sorted(opts.rng().sample(range(len(rows)), n))
        return [dict(rows[i]) for i in idxs]

    return _sample


def _compile_sampledistinct(op: SampleDistinct, opts: Options) -> BatchFn:
    n = op.count
    col = op.column

    def _sampledistinct(rows: list[Record], ctx: _Ctx) -> list[Record]:
        values: list[Any] = []
        seen: set[Any] = set()
        for r in rows:
            v = r.get(col)
            k = _hkey(v)
            if k not in seen:
                seen.add(k)
                values.append(v)
        if n < len(values):
            idxs = sorted(opts.rng().sample(range(len(values)), n))
            values = [values[i] for i in idxs]
        return [{col: v} for v in values]

    return _sampledistinct


_WINDOW_FUNCS = {"row_number", "prev", "next", "row_cumsum"}


def _extract_windows(node: Expr, acc: list[tuple[str, Call]]) -> Expr:
    """Rewrite *node*, replacing window-function calls with temp-column refs and
    appending ``(temp_name, call)`` to *acc*."""
    if isinstance(node, Call):
        if node.name in _WINDOW_FUNCS:
            name = f"__win{len(acc)}"
            acc.append((name, node))
            return Column(name)
        return Call(node.name, tuple(_extract_windows(a, acc) for a in node.args))
    if isinstance(node, Binary):
        return Binary(node.op, _extract_windows(node.left, acc),
                      _extract_windows(node.right, acc))
    if isinstance(node, Unary):
        return Unary(node.op, _extract_windows(node.operand, acc))
    if isinstance(node, Member):
        return Member(_extract_windows(node.target, acc), node.name)
    if isinstance(node, Index):
        return Index(_extract_windows(node.target, acc),
                     _extract_windows(node.key, acc))
    if isinstance(node, ExprList):
        return ExprList(tuple(_extract_windows(i, acc) for i in node.items))
    return node


def _compile_window_call(
    call: Call, opts: Options,
) -> Callable[[list[Record]], list[Any]]:
    """Compile a window-function call to ``rows -> per-row values``."""
    name = call.name
    args = call.args
    if name == "row_number":
        start = 1
        if len(args) >= 1:
            sv = _num_or_none(compile_expr(args[0], opts)({}))
            start = int(sv) if sv is not None else 1
        restart = compile_expr(args[1], opts) if len(args) >= 2 else None

        def _rownum(rows: list[Record]) -> list[Any]:
            out: list[Any] = []
            cur = start
            for i, row in enumerate(rows):
                if i == 0:
                    cur = start
                elif restart is not None and _truthy(restart(row)):
                    cur = start
                else:
                    cur += 1
                out.append(cur)
            return out
        return _rownum
    if name in ("prev", "next"):
        if not args:
            raise KqlCompileError(f"{name}() requires a column/expression argument")
        value = compile_expr(args[0], opts)
        offset = 1
        if len(args) >= 2:
            ov = _num_or_none(compile_expr(args[1], opts)({}))
            offset = int(ov) if ov is not None else 1
        default = compile_expr(args[2], opts)({}) if len(args) >= 3 else None
        sign = -1 if name == "prev" else 1

        def _shift(rows: list[Record]) -> list[Any]:
            n = len(rows)
            out2: list[Any] = []
            for i in range(n):
                j = i + sign * offset
                out2.append(value(rows[j]) if 0 <= j < n else default)
            return out2
        return _shift
    # row_cumsum
    if not args:
        raise KqlCompileError("row_cumsum() requires a term argument")
    term = compile_expr(args[0], opts)
    restart2 = compile_expr(args[1], opts) if len(args) >= 2 else None

    def _cumsum(rows: list[Record]) -> list[Any]:
        out3: list[Any] = []
        acc: float = 0
        for i, row in enumerate(rows):
            if i > 0 and restart2 is not None and _truthy(restart2(row)):
                acc = 0
            acc += _num_or_none(term(row)) or 0
            out3.append(acc)
        return out3
    return _cumsum


def _compile_serialize(op: Serialize, opts: Options) -> BatchFn:
    plan: list[tuple[str, Callable[[Record], Any],
                     list[tuple[str, Callable[[list[Record]], list[Any]]]]]] = []
    for name, expr in op.assignments:
        wins: list[tuple[str, Call]] = []
        rewritten = _extract_windows(expr, wins)
        fn = compile_expr(rewritten, opts)
        specs = [(tn, _compile_window_call(wc, opts)) for tn, wc in wins]
        plan.append((name, fn, specs))

    def _serialize(rows: list[Record], ctx: _Ctx) -> list[Record]:
        out = [dict(r) for r in rows]
        for name, fn, specs in plan:
            win_vals = {tn: run(out) for tn, run in specs}
            for i, r in enumerate(out):
                for tn, vals in win_vals.items():
                    r[tn] = vals[i]
                r[name] = fn(r)
                for tn in win_vals:
                    r.pop(tn, None)
        return out

    return _serialize


def _compile_mvapply(op: MvApply, opts: Options) -> BatchFn:
    specs: list[tuple[str, Callable[[Record], Any]]] = [
        (name, _col_getter(name) if src is None else compile_expr(src, opts))
        for name, src in op.columns
    ]
    consumed: set[str] = set()
    for name, src in op.columns:
        if src is None:
            consumed.add(name)
        elif isinstance(src, Column):
            consumed.add(src.name)
    run = _compile_subpipeline(op.operators, opts)

    def _mvapply(rows: list[Record], ctx: _Ctx) -> list[Record]:
        out: list[Record] = []
        for r in rows:
            seqs = [_mv_elements(fn(r)) for _, fn in specs]
            m = max((len(s) for s in seqs), default=0)
            subrows: list[Record] = []
            for i in range(m):
                sub: Record = {}
                for (name, _), s in zip(specs, seqs, strict=True):
                    sub[name] = s[i] if i < len(s) else None
                subrows.append(sub)
            base = {k: v for k, v in r.items() if k not in consumed}
            for res in run(subrows, ctx):
                merged = dict(base)
                merged.update(res)
                out.append(merged)
        return out

    return _mvapply


def _bin_index(av: Any, start: Any, step: Any, n: int) -> int | None:
    if av is None:
        return None
    try:
        ratio = (av - start) / step
    except (TypeError, ZeroDivisionError):
        return None
    bi = int(ratio) if ratio >= 0 else -1
    return bi if 0 <= bi < n else None


def _compile_makeseries(op: MakeSeries, opts: Options) -> BatchFn:
    start = compile_expr(op.start, opts)({})
    stop = compile_expr(op.stop, opts)({})
    step = compile_expr(op.step, opts)({})
    if start is None or stop is None or step is None:
        raise KqlCompileError("make-series 'from'/'to'/'step' must be constants")
    zero = start - start
    if step == zero:
        raise KqlCompileError("make-series 'step' must be non-zero")
    edges: list[Any] = []
    v = start
    while v < stop:
        edges.append(v)
        v = v + step
        if len(edges) > _RANGE_ROW_CAP:
            raise KqlCompileError("make-series produced too many bins")
    n_bins = len(edges)
    axis = op.axis
    agg_specs = [
        (name, _compile_aggregate(call, opts),
         (compile_expr(dflt, opts)({}) if dflt is not None else 0))
        for name, call, dflt in op.aggregates
    ]
    key_specs = [(name, compile_expr(expr, opts)) for name, expr in op.by_keys]

    def _makeseries(rows: list[Record], ctx: _Ctx) -> list[Record]:
        groups: dict[tuple, tuple[list[Any], list[list[Record]]]] = {}
        order: list[tuple] = []
        for r in rows:
            kvals = [kf(r) for _, kf in key_specs]
            key = tuple(_hkey(x) for x in kvals)
            if key not in groups:
                groups[key] = (kvals, [[] for _ in range(n_bins)])
                order.append(key)
            bi = _bin_index(r.get(axis), start, step, n_bins)
            if bi is not None:
                groups[key][1][bi].append(r)
        out: list[Record] = []
        for key in order:
            kvals, bins = groups[key]
            row: Record = {}
            for (kname, _), val in zip(key_specs, kvals, strict=True):
                row[kname] = val
            row[axis] = list(edges)
            for aname, afn, dflt in agg_specs:
                row[aname] = [afn(bins[i]) if bins[i] else dflt for i in range(n_bins)]
            out.append(row)
        return out

    return _makeseries


def _compile_batch(op: Operator, opts: Options, declared: set[str]) -> BatchFn:
    if isinstance(op, Summarize):
        return _compile_summarize(op, opts)
    if isinstance(op, Sort):
        keys = [(compile_expr(e, opts), desc) for e, desc in op.keys]
        return lambda rows, ctx: _sort_rows(rows, keys)
    if isinstance(op, Top):
        keys = [(compile_expr(e, opts), desc) for e, desc in op.keys]
        n = op.count
        return lambda rows, ctx: _sort_rows(rows, keys)[:n]
    if isinstance(op, Distinct):
        cols = op.columns

        def _distinct(rows: list[Record], ctx: _Ctx) -> list[Record]:
            seen: set[tuple] = set()
            out: list[Record] = []
            for r in rows:
                proj = {c: r.get(c) for c in cols}
                key = tuple(_hkey(proj[c]) for c in cols)
                if key not in seen:
                    seen.add(key)
                    out.append(proj)
            return out
        return _distinct
    if isinstance(op, Take):
        n = op.count
        return lambda rows, ctx: rows[:n]
    if isinstance(op, Join):
        return _compile_join(op, opts, declared)
    if isinstance(op, Union):
        return _compile_union(op, opts, declared)
    if isinstance(op, As):
        return _compile_as(op)
    if isinstance(op, Fork):
        return _compile_fork(op, opts)
    if isinstance(op, Partition):
        return _compile_partition(op, opts)
    if isinstance(op, GetSchema):
        return _compile_getschema()
    if isinstance(op, Count):
        return _compile_count()
    if isinstance(op, Sample):
        return _compile_sample(op, opts)
    if isinstance(op, SampleDistinct):
        return _compile_sampledistinct(op, opts)
    if isinstance(op, Serialize):
        return _compile_serialize(op, opts)
    if isinstance(op, MvApply):
        return _compile_mvapply(op, opts)
    raise KqlCompileError(f"cannot compile batch operator {type(op).__name__}")


_BATCH_OP_TYPES = (Summarize, Sort, Top, Distinct, Take, Join, Union, As, Fork,
                   Partition, GetSchema, Count, Sample, SampleDistinct, Serialize,
                   MvApply)


def _compile_step(op: Operator, opts: Options,
                  declared: set[str]) -> tuple[bool, Callable]:
    """Compile one pipeline step. Returns ``(is_batch, fn)`` — a batch step is
    ``(rows, ctx) -> rows``; a row step is ``record -> rows`` applied per row."""
    if isinstance(op, _BATCH_OP_TYPES):
        return (True, _compile_batch(op, opts, declared))
    return (False, _compile_operator(op, opts))


class CompiledQuery:
    """A compiled, reusable query (see ``streaming_kql.compile``)."""

    def __init__(self, query: Query, opts: Options,
                 schema: dict[str, str] | None = None):
        # Use a per-query Options clone so `let` bindings don't leak between
        # queries that share one Options instance (e.g. inside a Node).
        local = Options(now=opts.now, strict_types=opts.strict_types,
                        random_seed=opts.random_seed)
        for name, expr in query.lets:
            local._lets[name] = compile_expr(expr, local)({})
        for name, tnode in query.table_lets:
            local._tables[name] = _materialize_table(tnode, local)
        self._opts = local
        self._schema: tuple[tuple[str, str], ...] = tuple(
            (schema or {}).items())
        self._run = _compile_query_plan(query, local, self._schema)

    def transform(self, record: Record) -> list[Record]:
        """One record in → 0..N records out."""
        return self._run(record)

    def match(self, record: Record) -> Record | None:
        """Convenience for 1→≤1 queries. Raises if more than one row is emitted."""
        out = self.transform(record)
        if len(out) > 1:
            raise KqlEvalError("query emitted multiple rows; use transform()/stream()")
        return out[0] if out else None

    def stream(self, records: Iterable[Record]) -> Iterator[Record]:
        for rec in records:
            yield from self.transform(rec)
