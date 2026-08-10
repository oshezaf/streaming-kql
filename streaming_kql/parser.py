"""Lark-based parser for the streaming (stateless) KQL subset.

The grammar is declarative (see ``_GRAMMAR``) and a :class:`lark.Transformer`
lowers the parse tree into the AST in :mod:`streaming_kql.nodes`. The AST and
evaluator are parser-agnostic, so this grammar can grow toward the full DCR
surface (and, later, a stateful extension) without touching evaluation.

Recognized *stateful* operators (``summarize``, ``join``, ``sort`` …) and the
*deferred* 1->N operators (``mv-expand`` …) are rejected up front with a clear
:class:`KqlUnsupportedError`, before the grammar runs, so the grammar itself can
stay focused on what is actually supported.
"""
from __future__ import annotations

import re
from datetime import timedelta

from lark import Lark, Token, Transformer
from lark.exceptions import LarkError

from .errors import KqlCompileError, KqlUnsupportedError
from .nodes import (
    BagUnpack,
    Binary,
    Call,
    Column,
    ExprList,
    Extend,
    Index,
    Literal,
    Member,
    Parse,
    ParseKv,
    ParseSeg,
    Project,
    ProjectAway,
    ProjectItem,
    ProjectKeep,
    ProjectRename,
    ProjectReorder,
    Query,
    Unary,
    Where,
)

# Operators that act across records / reorder — unsupported in the streaming
# stateless model (see docs/SPEC.md §5.6).
_STATEFUL_OPERATORS = {
    "summarize", "join", "union", "sort", "order", "top", "top-nested",
    "make-series", "serialize", "partition", "scan", "range", "getschema",
    "count", "distinct", "sample-distinct", "mv-apply", "row_number",
}
# 1->N or bounded-state — deferred pending per-operator evaluation (SPEC §5.2).
_DEFERRED_OPERATORS = {"mv-expand", "mv-expand-array", "bag_unpack",
                       "take", "limit", "sample"}
_SUPPORTED_OPERATORS = {
    "where", "filter", "extend", "project", "project-away", "project-rename",
    "project-keep", "project-reorder", "parse", "parse-where", "parse-kv",
    "evaluate",
}
_SOURCE_HEADS = {"source", "print", "let"}

_GRAMMAR = r"""
start: let_stmt* query_body
let_stmt: "let" NAME "=" expr ";"
?query_body: q_source | q_print
q_source: "source" ("|" operator)*
q_print: print_op ("|" operator)*
print_op: "print" print_item ("," print_item)*
?print_item: NAME "=" expr -> print_named
           | expr           -> print_anon

?operator: where_op
         | extend_op
         | project_op
         | project_away_op
         | project_keep_op
         | project_reorder_op
         | project_rename_op
         | parse_op
         | parsekv_op
         | bagunpack_op

where_op: ("where"|"filter") expr
extend_op: "extend" assignment ("," assignment)*
assignment: NAME "=" expr
project_op: "project" project_item ("," project_item)*
?project_item: NAME "=" expr -> project_assign
             | NAME          -> project_keep
project_away_op: PROJECT_AWAY NAME ("," NAME)*
project_keep_op: PROJECT_KEEP NAME ("," NAME)*
project_reorder_op: PROJECT_REORDER NAME ("," NAME)*
project_rename_op: PROJECT_RENAME rename_pair ("," rename_pair)*
rename_pair: NAME "=" NAME
parse_op: PARSE_KW parse_kind? expr "with" parse_seg+
parse_kind: "kind" "=" NAME
?parse_seg: SQSTRING -> pseg_lit
          | DQSTRING -> pseg_lit
          | "*"      -> pseg_star
          | NAME (":" NAME)? -> pseg_col
parsekv_op: PARSEKV expr "as" "(" kv_col ("," kv_col)* ")" "with" "(" kv_opt ("," kv_opt)* ")"
kv_col: NAME ":" NAME
kv_opt: NAME "=" (SQSTRING|DQSTRING)
bagunpack_op: "evaluate" "bag_unpack" "(" NAME ("," bu_prefix)? ")"
bu_prefix: SQSTRING | DQSTRING

?expr: or_expr
?or_expr: and_expr | or_expr "or" and_expr -> or_
?and_expr: not_expr | and_expr "and" not_expr -> and_
?not_expr: cmp | "not" not_expr -> not_
?cmp: sum
    | sum COMP sum          -> cmp_sym
    | sum STROP sum         -> cmp_strop
    | sum "matches" "regex" sum -> cmp_matches
?sum: product | sum "+" product -> add | sum "-" product -> sub
?product: unary | product "*" unary -> mul | product "/" unary -> div | product "%" unary -> mod
?unary: postfix | "-" unary -> neg
?postfix: atom | postfix "." NAME -> member | postfix "[" expr "]" -> index
?atom: NUMBER            -> number
     | TIMESPAN          -> timespan_lit
     | DQSTRING          -> string
     | SQSTRING          -> string
     | "true"            -> true
     | "false"           -> false
     | "null"            -> null
     | NAME "(" arglist? ")" -> call
     | NAME              -> column
     | "(" expr ")"
     | "(" exprlist ")"
arglist: expr ("," expr)*
exprlist: expr ("," expr)+

COMP.2: /==|!=|<=|>=|=~|!~|<|>/
TIMESPAN.3: /\d+(\.\d+)?(ms|microseconds?|ticks?|[dhms])\b/
STROP.2: /!?(has_cs|has|contains_cs|contains|startswith_cs|startswith|endswith_cs|endswith|in)\b/
PROJECT_AWAY.2: /project-away\b/
PROJECT_KEEP.2: /project-keep\b/
PROJECT_REORDER.2: /project-reorder\b/
PROJECT_RENAME.2: /project-rename\b/
PARSE_KW.2: /parse-where\b|parse\b/
PARSEKV.3: /parse-kv\b/
NAME: /[a-zA-Z_][a-zA-Z0-9_]*/
NUMBER: /\d+(\.\d+)?([eE][+-]?\d+)?/
DQSTRING: /"([^"\\]|\\.)*"/
SQSTRING: /'([^'\\]|\\.)*'/
COMMENT: /\/\/[^\n]*/
%ignore /[ \t\r\n]+/
%ignore COMMENT
"""


def _unescape(tok: str) -> str:
    body = tok[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                        "'": "'", '"': '"'}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_TS_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds",
             "ms": "milliseconds"}


def _parse_timespan_literal(text: str) -> timedelta:
    m = re.match(r"(\d+(?:\.\d+)?)(ms|microseconds?|ticks?|[dhms])", text)
    assert m is not None
    amount = float(m.group(1))
    unit = m.group(2)
    if unit in _TS_UNITS:
        return timedelta(**{_TS_UNITS[unit]: amount})
    if unit.startswith("microsecond"):
        return timedelta(microseconds=amount)
    if unit.startswith("tick"):
        return timedelta(microseconds=amount / 10.0)  # 1 tick = 100 ns
    return timedelta()


class _ToAst(Transformer):
    def start(self, ch: list) -> Query:
        lets = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and len(c) == 3 and c[0] == "let")
        q = next(c for c in ch if isinstance(c, Query))
        return Query(operators=q.operators, lets=lets,
                     source_kind=q.source_kind, print_items=q.print_items)

    def let_stmt(self, ch: list) -> tuple:
        return ("let", str(ch[0]), ch[1])

    def q_source(self, ch: list) -> Query:
        return Query(operators=tuple(ch), source_kind="source")

    def q_print(self, ch: list) -> Query:
        items = ch[0]
        ops = tuple(ch[1:])
        return Query(operators=ops, source_kind="print", print_items=tuple(items))

    def print_op(self, ch: list) -> list:
        return list(ch)

    def print_named(self, ch: list) -> tuple:
        return (str(ch[0]), ch[1])

    def print_anon(self, ch: list) -> tuple:
        return (None, ch[0])

    # operators
    def where_op(self, ch: list) -> Where:
        return Where(ch[0])

    def extend_op(self, ch: list) -> Extend:
        return Extend(tuple(ch))

    def assignment(self, ch: list) -> tuple[str, object]:
        return (str(ch[0]), ch[1])

    def project_op(self, ch: list) -> Project:
        return Project(tuple(ch))

    def project_assign(self, ch: list) -> ProjectItem:
        return ProjectItem(str(ch[0]), ch[1])

    def project_keep(self, ch: list) -> ProjectItem:
        return ProjectItem(str(ch[0]), None)

    def project_away_op(self, ch: list) -> ProjectAway:
        names = tuple(str(t) for t in ch if isinstance(t, Token) and t.type == "NAME")
        return ProjectAway(names)

    def project_keep_op(self, ch: list) -> ProjectKeep:
        names = tuple(str(t) for t in ch if isinstance(t, Token) and t.type == "NAME")
        return ProjectKeep(names)

    def project_reorder_op(self, ch: list) -> ProjectReorder:
        names = tuple(str(t) for t in ch if isinstance(t, Token) and t.type == "NAME")
        return ProjectReorder(names)

    def project_rename_op(self, ch: list) -> ProjectRename:
        pairs = tuple(c for c in ch if isinstance(c, tuple))
        return ProjectRename(pairs)

    def rename_pair(self, ch: list) -> tuple[str, str]:
        return (str(ch[0]), str(ch[1]))

    # parse operator
    def parse_kind(self, ch: list) -> str:
        return str(ch[0]).lower()

    def pseg_lit(self, ch: list) -> ParseSeg:
        return ParseSeg("lit", _unescape(str(ch[0])))

    def pseg_star(self, ch: list) -> ParseSeg:
        return ParseSeg("star")

    def pseg_col(self, ch: list) -> ParseSeg:
        col_type = str(ch[1]) if len(ch) > 1 else None
        return ParseSeg("col", str(ch[0]), col_type)

    def parse_op(self, ch: list) -> Parse:
        kw = str(ch[0]).lower()
        rest = ch[1:]
        kind = "simple"
        if rest and isinstance(rest[0], str):
            kind = rest[0]
            rest = rest[1:]
        source = rest[0]
        segs = tuple(s for s in rest[1:] if isinstance(s, ParseSeg))
        return Parse(source, kind, segs, drop_unmatched=(kw == "parse-where"))

    # parse-kv operator
    def kv_col(self, ch: list) -> tuple:
        return ("col", str(ch[0]), str(ch[1]))

    def kv_opt(self, ch: list) -> tuple:
        return ("opt", str(ch[0]), _unescape(str(ch[1])))

    def parsekv_op(self, ch: list) -> ParseKv:
        source = next(c for c in ch if not isinstance(c, (Token, tuple)))
        cols = tuple((c[1], c[2]) for c in ch if isinstance(c, tuple) and c[0] == "col")
        opts = tuple((c[1], c[2]) for c in ch if isinstance(c, tuple) and c[0] == "opt")
        return ParseKv(source, cols, opts)

    def bu_prefix(self, ch: list) -> str:
        return _unescape(str(ch[0]))

    def bagunpack_op(self, ch: list) -> BagUnpack:
        col = str(ch[0])
        prefix = ch[1] if len(ch) > 1 and isinstance(ch[1], str) else ""
        return BagUnpack(col, prefix)

    # expressions
    def or_(self, ch: list) -> Binary:
        return Binary("or", ch[0], ch[1])

    def and_(self, ch: list) -> Binary:
        return Binary("and", ch[0], ch[1])

    def not_(self, ch: list) -> Unary:
        return Unary("not", ch[0])

    def cmp_sym(self, ch: list) -> Binary:
        return Binary(str(ch[1]), ch[0], ch[2])

    def cmp_strop(self, ch: list) -> Binary:
        return Binary(str(ch[1]).lower(), ch[0], ch[2])

    def cmp_matches(self, ch: list) -> Binary:
        return Binary("matches regex", ch[0], ch[1])

    def add(self, ch: list) -> Binary:
        return Binary("+", ch[0], ch[1])

    def sub(self, ch: list) -> Binary:
        return Binary("-", ch[0], ch[1])

    def mul(self, ch: list) -> Binary:
        return Binary("*", ch[0], ch[1])

    def div(self, ch: list) -> Binary:
        return Binary("/", ch[0], ch[1])

    def mod(self, ch: list) -> Binary:
        return Binary("%", ch[0], ch[1])

    def neg(self, ch: list) -> Unary:
        return Unary("-", ch[0])

    def member(self, ch: list) -> Member:
        return Member(ch[0], str(ch[1]))

    def index(self, ch: list) -> Index:
        return Index(ch[0], ch[1])

    def number(self, ch: list) -> Literal:
        t = str(ch[0])
        return Literal(float(t) if any(c in t for c in ".eE") else int(t))

    def timespan_lit(self, ch: list) -> Literal:
        return Literal(_parse_timespan_literal(str(ch[0])))

    def string(self, ch: list) -> Literal:
        return Literal(_unescape(str(ch[0])))

    def true(self, ch: list) -> Literal:
        return Literal(True)

    def false(self, ch: list) -> Literal:
        return Literal(False)

    def null(self, ch: list) -> Literal:
        return Literal(None)

    def call(self, ch: list) -> Call:
        name = str(ch[0]).lower()
        args = ch[1] if len(ch) > 1 and ch[1] is not None else []
        return Call(name, tuple(args))

    def column(self, ch: list) -> Column:
        return Column(str(ch[0]))

    def arglist(self, ch: list) -> list:
        return list(ch)

    def exprlist(self, ch: list) -> ExprList:
        return ExprList(tuple(ch))


_PARSER = Lark(_GRAMMAR, parser="earley", maybe_placeholders=True)
_TRANSFORMER = _ToAst()

_OP_LEADING = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_-]*)")


def _split_pipes(text: str) -> list[str]:
    """Split top-level ``|`` boundaries, ignoring pipes inside string literals."""
    segs: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c == "|":
            segs.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def _precheck(text: str) -> None:
    """Give friendly errors for stateful/deferred/unknown operators before the
    grammar runs (which only recognizes supported operators)."""
    segs = _split_pipes(text)
    first = segs[0].strip()
    head = first.split()[0].lower() if first.split() else ""
    if head not in _SOURCE_HEADS:
        raise KqlCompileError(
            "a streaming-kql query must start with 'source', 'print', or 'let'")
    for seg in segs[1:]:
        m = _OP_LEADING.match(seg)
        if not m:
            raise KqlCompileError("expected an operator after '|'")
        name = m.group(1).lower()
        if name in _SUPPORTED_OPERATORS:
            continue
        if name in _STATEFUL_OPERATORS:
            raise KqlUnsupportedError(
                f"operator '{name}' is stateful (acts across records) and is not "
                "supported in the streaming stateless model")
        if name in _DEFERRED_OPERATORS:
            raise KqlUnsupportedError(
                f"operator '{name}' is not yet implemented (under evaluation)")
        raise KqlCompileError(f"unknown operator '{name}'")


def parse(text: str) -> Query:
    """Parse KQL source text into a :class:`Query` AST."""
    _precheck(text)
    try:
        tree = _PARSER.parse(text)
    except LarkError as e:
        raise KqlCompileError(f"could not parse query: {e}") from e
    result = _TRANSFORMER.transform(tree)
    assert isinstance(result, Query)
    return result
