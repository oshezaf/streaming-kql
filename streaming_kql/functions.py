"""Scalar function library + registry.

M0 ships a starter set of the Azure Monitor DCR scalar functions. The registry
is extensible at runtime via :func:`register` (exposed publicly as
``streaming_kql.function``), so callers can add domain helpers without forking.
"""
from __future__ import annotations

import base64 as _base64
import csv as _csv
import decimal as _decimal
import hashlib as _hashlib
import ipaddress as _ipaddress
import json
import math as _math
import re
import urllib.parse as _urlparse
import uuid as _uuid
import xml.etree.ElementTree as _ET
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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


def _xml_to_obj(el: _ET.Element) -> Any:
    obj: dict[str, Any] = {}
    for k, val in el.attrib.items():
        obj[f"@{k}"] = val
    for child in list(el):
        co = _xml_to_obj(child)
        if child.tag in obj:
            if not isinstance(obj[child.tag], list):
                obj[child.tag] = [obj[child.tag]]
            obj[child.tag].append(co)
        else:
            obj[child.tag] = co
    text = (el.text or "").strip()
    if text:
        if obj:
            obj["#text"] = text
        else:
            return text
    return obj if obj else None


@register("parse_xml")
def _parse_xml(v: Any) -> Any:
    if v is None:
        return None
    try:
        root = _ET.fromstring(_s(v))
    except _ET.ParseError:
        return None
    return {root.tag: _xml_to_obj(root)}


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


@register("dayofweek")
def _dayofweek(v: Any) -> Any:
    from datetime import timedelta
    d = _dt(v)
    if not d:
        return None
    return timedelta(days=(d.weekday() + 1) % 7)  # KQL: Sunday=0 .. Saturday=6


_DIFF_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
                 "week": 604800}


@register("datetime_diff")
def _datetime_diff(part: Any, a: Any, b: Any) -> int | None:
    da, db = _dt(a), _dt(b)
    p = _s(part).lower()
    if da is None or db is None:
        return None
    if p in _DIFF_SECONDS:
        return int((da - db).total_seconds() // _DIFF_SECONDS[p])
    if p == "month":
        return (da.year - db.year) * 12 + (da.month - db.month)
    if p == "year":
        return da.year - db.year
    return None


_FMT_TOKENS = [("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"),
               ("mm", "%M"), ("ss", "%S")]


@register("format_datetime")
def _format_datetime(v: Any, fmt: Any) -> str | None:
    d = _dt(v)
    if d is None:
        return None
    out = _s(fmt)
    for kql_tok, py_tok in _FMT_TOKENS:
        out = out.replace(kql_tok, py_tok)
    return d.strftime(out)


# --- more string / array / dynamic ------------------------------------------
@register("trim")
def _trim(pattern: Any, source: Any) -> str | None:
    if source is None:
        return None
    p = _s(pattern)
    return re.sub(f"^(?:{p})+|(?:{p})+$", "", _s(source))


@register("trim_start")
def _trim_start(pattern: Any, source: Any) -> str | None:
    if source is None:
        return None
    return re.sub(f"^(?:{_s(pattern)})+", "", _s(source))


@register("trim_end")
def _trim_end(pattern: Any, source: Any) -> str | None:
    if source is None:
        return None
    return re.sub(f"(?:{_s(pattern)})+$", "", _s(source))


@register("strcat_array")
def _strcat_array(arr: Any, delim: Any) -> str | None:
    if not isinstance(arr, list):
        return None
    return _s(delim).join(_s(x) for x in arr)


@register("reverse")
def _reverse(v: Any) -> Any:
    if isinstance(v, list):
        return list(reversed(v))
    if v is None:
        return None
    return _s(v)[::-1]


@register("sqrt")
def _sqrt(v: Any) -> Any:
    n = _num(v)
    return None if n is None or n < 0 else _math.sqrt(n)


@register("tohex")
def _tohex(v: Any) -> str | None:
    n = _toint(v)
    if n is None:
        return None
    return format(n & (2 ** 64 - 1) if n < 0 else n, "x")


@register("array_index_of")
def _array_index_of(arr: Any, value: Any) -> int:
    if isinstance(arr, list):
        try:
            return arr.index(value)
        except ValueError:
            return -1
    return -1


@register("array_slice")
def _array_slice(arr: Any, start: Any, end: Any) -> Any:
    if not isinstance(arr, list):
        return None
    return arr[int(start):int(end) + 1]


@register("bag_keys")
def _bag_keys(v: Any) -> Any:
    return list(v.keys()) if isinstance(v, dict) else None


def _distinct(seq: Any) -> list:
    out: list[Any] = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


@register("set_union")
def _set_union(*arrays: Any) -> list:
    out: list[Any] = []
    for a in arrays:
        if isinstance(a, list):
            out.extend(a)
    return _distinct(out)


@register("set_intersect")
def _set_intersect(first: Any, *rest: Any) -> list:
    if not isinstance(first, list):
        return []
    others = [set(map(_hashable, a)) for a in rest if isinstance(a, list)]
    return _distinct([x for x in first if all(_hashable(x) in o for o in others)])


@register("set_difference")
def _set_difference(first: Any, *rest: Any) -> list:
    if not isinstance(first, list):
        return []
    remove: set[Any] = set()
    for a in rest:
        if isinstance(a, list):
            remove.update(_hashable(x) for x in a)
    return _distinct([x for x in first if _hashable(x) not in remove])


def _hashable(x: Any) -> Any:
    try:
        hash(x)
        return x
    except TypeError:
        return _s(x)


# --- bitwise -----------------------------------------------------------------
@register("binary_and")
def _binary_and(a: Any, b: Any) -> int | None:
    x, y = _toint(a), _toint(b)
    return None if x is None or y is None else x & y


@register("binary_or")
def _binary_or(a: Any, b: Any) -> int | None:
    x, y = _toint(a), _toint(b)
    return None if x is None or y is None else x | y


@register("binary_xor")
def _binary_xor(a: Any, b: Any) -> int | None:
    x, y = _toint(a), _toint(b)
    return None if x is None or y is None else x ^ y


@register("binary_not")
def _binary_not(a: Any) -> int | None:
    x = _toint(a)
    return None if x is None else ~x


@register("binary_shift_left")
def _binary_shift_left(a: Any, n: Any) -> int | None:
    x, y = _toint(a), _toint(n)
    return None if x is None or y is None else x << y


@register("binary_shift_right")
def _binary_shift_right(a: Any, n: Any) -> int | None:
    x, y = _toint(a), _toint(n)
    return None if x is None or y is None else x >> y


# --- conversion / type -------------------------------------------------------
@register("toguid")
def _toguid(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return str(_uuid.UUID(_s(v)))
    except (ValueError, AttributeError):
        return None


@register("todecimal")
def _todecimal(v: Any) -> Any:
    if v is None or v == "":
        return None
    try:
        return _decimal.Decimal(_s(v))
    except (_decimal.InvalidOperation, ValueError):
        return None


@register("isascii")
def _isascii(v: Any) -> bool:
    return isinstance(v, str) and v.isascii()


# --- hashing -----------------------------------------------------------------
@register("hash_md5")
def _hash_md5(v: Any) -> str:
    return _hashlib.md5(_s(v).encode("utf-8")).hexdigest()


@register("hash_sha1")
def _hash_sha1(v: Any) -> str:
    return _hashlib.sha1(_s(v).encode("utf-8")).hexdigest()


# --- math (trig etc.) --------------------------------------------------------
def _math1(fn: Callable[[float], float]) -> Callable[[Any], Any]:
    def wrap(v: Any) -> Any:
        n = _num(v)
        try:
            return None if n is None else fn(float(n))
        except (ValueError, OverflowError):
            return None
    return wrap


for _name, _fn in {
    "sin": _math.sin, "cos": _math.cos, "tan": _math.tan,
    "asin": _math.asin, "acos": _math.acos, "atan": _math.atan,
    "degrees": _math.degrees, "radians": _math.radians,
    "gamma": _math.gamma, "log_gamma": _math.lgamma,
}.items():
    _REGISTRY[_name] = _math1(_fn)


@register("atan2")
def _atan2(y: Any, x: Any) -> Any:
    a, b = _num(y), _num(x)
    return None if a is None or b is None else _math.atan2(float(a), float(b))


@register("gcd")
def _gcd(a: Any, b: Any) -> int | None:
    x, y = _toint(a), _toint(b)
    return None if x is None or y is None else _math.gcd(x, y)


@register("lcm")
def _lcm(a: Any, b: Any) -> int | None:
    x, y = _toint(a), _toint(b)
    if x is None or y is None:
        return None
    return 0 if x == 0 or y == 0 else abs(x * y) // _math.gcd(x, y)


# --- string ------------------------------------------------------------------
@register("strcmp")
def _strcmp(a: Any, b: Any) -> int:
    x, y = _s(a), _s(b)
    return (x > y) - (x < y)


@register("translate")
def _translate(search_list: Any, replace_list: Any, source: Any) -> str | None:
    if source is None:
        return None
    src_chars = _s(search_list)
    rep_chars = _s(replace_list)
    mapping: dict[int, str | None] = {}
    for i, ch in enumerate(src_chars):
        mapping[ord(ch)] = rep_chars[i] if i < len(rep_chars) else None
    return _s(source).translate(mapping)


@register("indexof_regex")
def _indexof_regex(source: Any, pattern: Any, start: Any = 0) -> int:
    if source is None:
        return -1
    m = re.search(_s(pattern), _s(source)[int(start or 0):])
    return -1 if not m else m.start() + int(start or 0)


@register("parse_csv")
def _parse_csv(v: Any) -> list:
    if v is None:
        return []
    line = _s(v).splitlines()[0] if _s(v) else ""
    return next(_csv.reader([line]), [])


# --- URL ---------------------------------------------------------------------
@register("url_encode_component")
def _url_encode_component(v: Any) -> str:
    return _urlparse.quote(_s(v), safe="")


@register("url_encode")
def _url_encode(v: Any) -> str:
    return _urlparse.quote_plus(_s(v))


@register("url_decode")
def _url_decode(v: Any) -> str:
    return _urlparse.unquote_plus(_s(v))


@register("parse_urlquery")
def _parse_urlquery(v: Any) -> dict:
    q = _s(v)
    if "?" in q:
        q = q.split("?", 1)[1]
    params = {k: vals[0] for k, vals in _urlparse.parse_qs(q).items()}
    return {"Query Parameters": params}


@register("parse_url")
def _parse_url(v: Any) -> dict:
    u = _urlparse.urlsplit(_s(v))
    params = {k: vals[0] for k, vals in _urlparse.parse_qs(u.query).items()}
    return {
        "Scheme": u.scheme,
        "Host": u.hostname or "",
        "Port": str(u.port) if u.port else "",
        "Path": u.path,
        "Username": u.username or "",
        "Password": u.password or "",
        "Query Parameters": params,
        "Fragment": u.fragment,
    }


# --- IPv4 --------------------------------------------------------------------
def _ipv4(v: Any) -> Any:
    try:
        s = _s(v).split("/")[0].strip()
        return _ipaddress.IPv4Address(s)
    except (ValueError, _ipaddress.AddressValueError):
        return None


@register("parse_ipv4")
def _parse_ipv4(v: Any) -> int | None:
    ip = _ipv4(v)
    return int(ip) if ip is not None else None


@register("format_ipv4")
def _format_ipv4(v: Any, prefix: Any = 32) -> str | None:
    ip = _ipv4(v)
    if ip is None:
        try:
            ip = _ipaddress.IPv4Address(int(v))
        except (ValueError, TypeError):
            return None
    p = int(prefix) if prefix is not None else 32
    net = _ipaddress.IPv4Network(f"{ip}/{p}", strict=False)
    return str(net.network_address)


@register("ipv4_is_private")
def _ipv4_is_private(v: Any) -> bool | None:
    ip = _ipv4(v)
    return None if ip is None else ip.is_private


@register("ipv4_netmask_suffix")
def _ipv4_netmask_suffix(v: Any) -> int | None:
    s = _s(v)
    if "/" in s:
        try:
            return int(s.split("/", 1)[1])
        except ValueError:
            return None
    return 32 if _ipv4(v) is not None else None


@register("ipv4_is_in_range")
def _ipv4_is_in_range(ip: Any, ip_range: Any) -> bool | None:
    addr = _ipv4(ip)
    if addr is None:
        return None
    try:
        net = _ipaddress.IPv4Network(_s(ip_range), strict=False)
    except (ValueError, _ipaddress.AddressValueError):
        return None
    return addr in net


@register("ipv4_compare")
def _ipv4_compare(a: Any, b: Any, prefix: Any = 32) -> int | None:
    x, y = _ipv4(a), _ipv4(b)
    if x is None or y is None:
        return None
    p = int(prefix) if prefix is not None else 32
    mask = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if 0 <= p <= 32 else 0xFFFFFFFF
    xi, yi = int(x) & mask, int(y) & mask
    return (xi > yi) - (xi < yi)


# --- datetime (more) ---------------------------------------------------------
@register("make_datetime")
def _make_datetime(year: Any, month: Any, day: Any,
                   hour: Any = 0, minute: Any = 0, second: Any = 0) -> datetime | None:
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0),
                        int(minute or 0), int(second or 0), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


@register("make_timespan")
def _make_timespan(hours: Any, minutes: Any, seconds: Any = 0) -> Any:
    try:
        return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    except (ValueError, TypeError):
        return None


@register("weekofyear")
@register("week_of_year")
def _weekofyear(v: Any) -> int | None:
    d = _dt(v)
    return d.isocalendar()[1] if d else None


@register("endofday")
def _endofday(v: Any) -> datetime | None:
    d = _dt(v)
    return d.replace(hour=23, minute=59, second=59, microsecond=999999) if d else None


@register("endofmonth")
def _endofmonth(v: Any) -> datetime | None:
    d = _dt(v)
    if not d:
        return None
    if d.month == 12:
        nxt = d.replace(year=d.year + 1, month=1, day=1)
    else:
        nxt = d.replace(month=d.month + 1, day=1)
    return (nxt - timedelta(microseconds=1)).replace(hour=23, minute=59, second=59,
                                                      microsecond=999999)


@register("endofyear")
def _endofyear(v: Any) -> datetime | None:
    d = _dt(v)
    return d.replace(month=12, day=31, hour=23, minute=59, second=59,
                     microsecond=999999) if d else None


@register("datetime_part")
def _datetime_part(part: Any, v: Any) -> int | None:
    d = _dt(v)
    if not d:
        return None
    p = _s(part).lower()
    return {
        "year": d.year, "month": d.month, "day": d.day, "hour": d.hour,
        "minute": d.minute, "second": d.second,
        "dayofyear": d.timetuple().tm_yday, "weekofyear": d.isocalendar()[1],
    }.get(p)


@register("datetime_add")
def _datetime_add(part: Any, amount: Any, v: Any) -> datetime | None:
    d = _dt(v)
    n = _toint(amount)
    if d is None or n is None:
        return None
    p = _s(part).lower()
    if p == "day":
        return d + timedelta(days=n)
    if p == "hour":
        return d + timedelta(hours=n)
    if p == "minute":
        return d + timedelta(minutes=n)
    if p == "second":
        return d + timedelta(seconds=n)
    if p == "week":
        return d + timedelta(weeks=n)
    if p == "month":
        total = d.year * 12 + (d.month - 1) + n
        year, month = divmod(total, 12)
        return d.replace(year=year, month=month + 1)
    if p == "year":
        return d.replace(year=d.year + n)
    return None


@register("unixtime_seconds_todatetime")
def _unix_s(v: Any) -> datetime | None:
    n = _num(v)
    return datetime.fromtimestamp(float(n), tz=timezone.utc) if n is not None else None


@register("unixtime_milliseconds_todatetime")
def _unix_ms(v: Any) -> datetime | None:
    n = _num(v)
    return datetime.fromtimestamp(float(n) / 1e3, tz=timezone.utc) if n is not None else None


@register("unixtime_microseconds_todatetime")
def _unix_us(v: Any) -> datetime | None:
    n = _num(v)
    return datetime.fromtimestamp(float(n) / 1e6, tz=timezone.utc) if n is not None else None


# --- dynamic / array (more) --------------------------------------------------
@register("bag_merge")
def _bag_merge(*bags: Any) -> dict:
    out: dict[str, Any] = {}
    for b in bags:
        if isinstance(b, dict):
            for k, v in b.items():
                out.setdefault(k, v)  # first non-null wins
    return out


@register("bag_remove_keys")
def _bag_remove_keys(bag: Any, keys: Any) -> Any:
    if not isinstance(bag, dict):
        return bag
    drop = set(keys) if isinstance(keys, list) else set()
    return {k: v for k, v in bag.items() if k not in drop}


def _sort_key(x: Any) -> tuple:
    return (x is None, x if x is not None else "")


@register("array_sort_asc")
def _array_sort_asc(arr: Any) -> Any:
    if not isinstance(arr, list):
        return None
    try:
        return sorted(arr, key=_sort_key)
    except TypeError:
        return sorted(arr, key=lambda x: (x is None, _s(x)))


@register("array_sort_desc")
def _array_sort_desc(arr: Any) -> Any:
    s = _array_sort_asc(arr)
    return None if s is None else list(reversed(s))


@register("array_reverse")
def _array_reverse(arr: Any) -> Any:
    return list(reversed(arr)) if isinstance(arr, list) else None


@register("array_sum")
def _array_sum(arr: Any) -> Any:
    if not isinstance(arr, list):
        return None
    nums = [x for x in arr if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return sum(nums) if nums else 0


@register("array_rotate_left")
def _array_rotate_left(arr: Any, n: Any) -> Any:
    if not isinstance(arr, list) or not arr:
        return arr
    k = int(n or 0) % len(arr)
    return arr[k:] + arr[:k]


@register("array_rotate_right")
def _array_rotate_right(arr: Any, n: Any) -> Any:
    if not isinstance(arr, list) or not arr:
        return arr
    k = int(n or 0) % len(arr)
    return arr[-k:] + arr[:-k] if k else list(arr)


@register("array_split")
def _array_split(arr: Any, index: Any) -> Any:
    if not isinstance(arr, list):
        return None
    idxs = index if isinstance(index, list) else [index]
    points = [0] + [int(i) for i in idxs] + [len(arr)]
    return [arr[points[i]:points[i + 1]] for i in range(len(points) - 1)]


# --- CEF (transformation-only) ----------------------------------------------
@register("parse_cef_dictionary")
def _parse_cef_dictionary(v: Any) -> dict:
    text = _s(v)
    if text.startswith("CEF:"):
        parts = text.split("|")
        header = {
            "Version": parts[0][4:] if len(parts) > 0 else "",
            "DeviceVendor": parts[1] if len(parts) > 1 else "",
            "DeviceProduct": parts[2] if len(parts) > 2 else "",
            "DeviceVersion": parts[3] if len(parts) > 3 else "",
            "SignatureId": parts[4] if len(parts) > 4 else "",
            "Name": parts[5] if len(parts) > 5 else "",
            "Severity": parts[6] if len(parts) > 6 else "",
        }
        ext = parts[7] if len(parts) > 7 else ""
    else:
        header = {}
        ext = text
    extension = dict(re.findall(r"(\w+)=((?:[^=]|=(?!\w+=))*?)(?=\s+\w+=|$)", ext))
    result: dict[str, Any] = dict(header)
    result["Extension"] = {k: v.strip() for k, v in extension.items()}
    return result




