"""Scalar function library + registry.

M0 ships a starter set of the Azure Monitor DCR scalar functions. The registry
is extensible at runtime via :func:`register` (exposed publicly as
``streaming_kql.function``), so callers can add domain helpers without forking.
"""
from __future__ import annotations

import base64 as _base64
import hashlib as _hashlib
import json
import math as _math
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[name.lower()] = fn
        return fn
    return deco


def get(name: str) -> Callable[..., Any] | None:
    return _REGISTRY.get(name.lower())


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# --- string ------------------------------------------------------------------
@register("strcat")
def _strcat(*args: Any) -> str:
    return "".join(_s(a) for a in args)


@register("strcat_delim")
def _strcat_delim(delim: Any, *args: Any) -> str:
    return _s(delim).join(_s(a) for a in args)


@register("strlen")
def _strlen(v: Any) -> int | None:
    return None if v is None else len(_s(v))


@register("toupper")
def _toupper(v: Any) -> str | None:
    return None if v is None else _s(v).upper()


@register("tolower")
def _tolower(v: Any) -> str | None:
    return None if v is None else _s(v).lower()


@register("substring")
def _substring(v: Any, start: int, length: int | None = None) -> str | None:
    if v is None:
        return None
    s = _s(v)
    start = int(start)
    if start < 0:
        start = max(0, len(s) + start)
    return s[start:] if length is None else s[start:start + int(length)]


@register("split")
def _split(v: Any, delim: Any, index: int | None = None) -> Any:
    parts = _s(v).split(_s(delim))
    if index is None:
        return parts
    idx = int(index)
    return parts[idx] if -len(parts) <= idx < len(parts) else None


@register("replace_string")
@register("replace")
def _replace(v: Any, old: Any, new: Any) -> str | None:
    return None if v is None else _s(v).replace(_s(old), _s(new))


@register("replace_regex")
def _replace_regex(v: Any, pattern: Any, rewrite: Any) -> str | None:
    if v is None:
        return None
    # KQL rewrite uses \0..\9 backreferences
    rw = re.sub(r"\\(\d)", r"\\g<\1>", _s(rewrite))
    return re.sub(_s(pattern), rw, _s(v))


@register("extract")
def _extract(pattern: Any, group: int, source: Any) -> str | None:
    if source is None:
        return None
    m = re.search(_s(pattern), _s(source))
    if not m:
        return None
    try:
        return m.group(int(group))
    except (IndexError, re.error):
        return None


@register("indexof")
def _indexof(source: Any, lookup: Any) -> int:
    return _s(source).find(_s(lookup))


@register("countof")
def _countof(source: Any, search: Any) -> int:
    return _s(source).count(_s(search))


@register("isempty")
def _isempty(v: Any) -> bool:
    return v is None or v == ""


@register("isnotempty")
def _isnotempty(v: Any) -> bool:
    return not (v is None or v == "")


# --- type / conditional ------------------------------------------------------
@register("isnull")
def _isnull(v: Any) -> bool:
    return v is None


@register("isnotnull")
def _isnotnull(v: Any) -> bool:
    return v is not None


@register("coalesce")
def _coalesce(*args: Any) -> Any:
    for a in args:
        if a is not None and a != "":
            return a
    return None


@register("iff")
@register("iif")
def _iif(cond: Any, a: Any, b: Any) -> Any:
    return a if cond is True else b


@register("case")
def _case(*args: Any) -> Any:
    # case(pred1, val1, pred2, val2, ..., else)
    it = list(args)
    default = it.pop() if len(it) % 2 == 1 else None
    for i in range(0, len(it) - 1, 2):
        if it[i] is True:
            return it[i + 1]
    return default


@register("max_of")
def _max_of(*args: Any) -> Any:
    vals = [a for a in args if a is not None]
    return max(vals) if vals else None


@register("min_of")
def _min_of(*args: Any) -> Any:
    vals = [a for a in args if a is not None]
    return min(vals) if vals else None


@register("gettype")
def _gettype(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "long"
    if isinstance(v, float):
        return "real"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, dict)):
        return "array" if isinstance(v, list) else "dynamic"
    return "string"


# --- conversion --------------------------------------------------------------
@register("tostring")
def _tostring(v: Any) -> str:
    return _s(v)


@register("toint")
@register("tolong")
def _toint(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v)) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        return None


@register("toreal")
@register("todouble")
def _toreal(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


@register("tobool")
def _tobool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        return None
    if isinstance(v, (int, float)):
        return v != 0
    return None


@register("todatetime")
def _todatetime(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    try:
        s = _s(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --- dynamic -----------------------------------------------------------------
@register("parse_json")
@register("todynamic")
def _parse_json(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(_s(v))
    except (ValueError, TypeError):
        return None


@register("array_length")
def _array_length(v: Any) -> int | None:
    return len(v) if isinstance(v, (list, str)) else None


# --- datetime (context-dependent) -------------------------------------------
# now()/ago() need the evaluation clock; they are handled specially in the
# evaluator (see evaluator._CLOCK_FUNCS) and registered here as placeholders so
# name resolution/validation still recognizes them.
_CLOCK_FUNCS = {"now", "ago"}


# --- math --------------------------------------------------------------------
def _num(v: Any) -> float | int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@register("abs")
def _abs(v: Any) -> Any:
    n = _num(v)
    return None if n is None else abs(n)


@register("ceiling")
def _ceiling(v: Any) -> Any:
    n = _num(v)
    return None if n is None else _math.ceil(n)


@register("floor")
@register("bin")
def _floor(v: Any, roundto: Any = 1) -> Any:
    n = _num(v)
    r = _num(roundto)
    if n is None or r is None or r == 0:
        return None
    return _math.floor(n / r) * r


@register("round")
def _round(v: Any, precision: Any = 0) -> Any:
    n = _num(v)
    return None if n is None else round(n, int(precision or 0))


@register("sign")
def _sign(v: Any) -> Any:
    n = _num(v)
    if n is None:
        return None
    return (n > 0) - (n < 0)


@register("pow")
def _pow(a: Any, b: Any) -> Any:
    x, y = _num(a), _num(b)
    return None if x is None or y is None else x ** y


@register("exp")
def _exp(v: Any) -> Any:
    n = _num(v)
    return None if n is None else _math.exp(n)


@register("exp2")
def _exp2(v: Any) -> Any:
    n = _num(v)
    return None if n is None else 2.0 ** n


@register("exp10")
def _exp10(v: Any) -> Any:
    n = _num(v)
    return None if n is None else 10.0 ** n


@register("log")
def _log(v: Any) -> Any:
    n = _num(v)
    return None if n is None or n <= 0 else _math.log(n)


@register("log2")
def _log2(v: Any) -> Any:
    n = _num(v)
    return None if n is None or n <= 0 else _math.log2(n)


@register("log10")
def _log10(v: Any) -> Any:
    n = _num(v)
    return None if n is None or n <= 0 else _math.log10(n)


@register("isnan")
def _isnan(v: Any) -> bool:
    return isinstance(v, float) and _math.isnan(v)


@register("isinf")
def _isinf(v: Any) -> bool:
    return isinstance(v, float) and _math.isinf(v)


@register("isfinite")
def _isfinite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and _math.isfinite(v)


# --- hashing / encoding ------------------------------------------------------
@register("hash_sha256")
def _hash_sha256(v: Any) -> str:
    return _hashlib.sha256(_s(v).encode("utf-8")).hexdigest()


@register("base64_encode_tostring")
@register("base64_encodestring")
def _b64_encode(v: Any) -> str:
    return _base64.b64encode(_s(v).encode("utf-8")).decode("ascii")


@register("base64_decode_tostring")
@register("base64_decodestring")
def _b64_decode(v: Any) -> str | None:
    try:
        return _base64.b64decode(_s(v)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


# --- more string / dynamic ---------------------------------------------------
@register("extract_all")
def _extract_all(pattern: Any, source: Any) -> list | None:
    if source is None:
        return None
    rx = re.compile(_s(pattern))
    matches = list(rx.finditer(_s(source)))
    if rx.groups == 0:
        return [m.group(0) for m in matches]
    if rx.groups == 1:
        return [m.group(1) for m in matches]
    return [list(m.groups()) for m in matches]


@register("array_concat")
def _array_concat(*arrays: Any) -> list:
    out: list[Any] = []
    for a in arrays:
        if isinstance(a, list):
            out.extend(a)
        elif a is not None:
            out.append(a)
    return out


@register("pack_array")
def _pack_array(*args: Any) -> list:
    return list(args)


@register("pack")
def _pack(*args: Any) -> dict:
    return {_s(args[i]): args[i + 1] for i in range(0, len(args) - 1, 2)}


# --- datetime parts ----------------------------------------------------------
def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    return _todatetime(v)


@register("getyear")
def _getyear(v: Any) -> int | None:
    d = _dt(v)
    return d.year if d else None


@register("getmonth")
@register("monthofyear")
def _getmonth(v: Any) -> int | None:
    d = _dt(v)
    return d.month if d else None


@register("dayofmonth")
def _dayofmonth(v: Any) -> int | None:
    d = _dt(v)
    return d.day if d else None


@register("hourofday")
def _hourofday(v: Any) -> int | None:
    d = _dt(v)
    return d.hour if d else None


@register("startofday")
def _startofday(v: Any) -> datetime | None:
    d = _dt(v)
    return d.replace(hour=0, minute=0, second=0, microsecond=0) if d else None


@register("startofmonth")
def _startofmonth(v: Any) -> datetime | None:
    d = _dt(v)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0) if d else None


@register("startofyear")
def _startofyear(v: Any) -> datetime | None:
    d = _dt(v)
    return d.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0) if d else None


@register("dayofyear")
def _dayofyear(v: Any) -> int | None:
    d = _dt(v)
    return d.timetuple().tm_yday if d else None


_TIMESPAN_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*(d|h|m|s|ms|microsecond|tick)?\s*$", re.IGNORECASE)
_TIMESPAN_UNIT = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds",
                  "ms": "milliseconds"}


@register("totimespan")
def _totimespan(v: Any) -> Any:
    from datetime import timedelta
    if v is None or v == "":
        return None
    if isinstance(v, timedelta):
        return v
    m = _TIMESPAN_RE.match(_s(v))
    if not m:
        return None
    amount = float(m.group(1))
    unit = (m.group(2) or "d").lower()
    kw = _TIMESPAN_UNIT.get(unit)
    if kw is None:
        return None
    return timedelta(**{kw: amount})


