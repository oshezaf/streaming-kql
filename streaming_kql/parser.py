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
    ParseSeg,
    Partition,
    Project,
    ProjectAway,
    ProjectItem,
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

# Recognized KQL operators that are not yet implemented. Under the per-record
# paradigm each has a well-defined *stateless per-record* form (it operates on
# the current record's row-set, not across records), so these are all
# implementable — just not built yet. ``sample``/``sample-distinct`` are also
# non-deterministic (random) and would be opt-in. See docs/SPEC.md §5.6.
_DEFERRED_OPERATORS = {
    "scan", "top-nested",
}
_SUPPORTED_OPERATORS = {
    "where", "filter", "extend", "project", "project-away", "project-rename",
    "project-keep", "project-reorder", "parse", "parse-where", "parse-kv",
    "evaluate", "lookup", "mv-expand", "mvexpand", "union",
    "summarize", "sort", "order", "top", "distinct", "take", "limit", "join",
    "as", "fork", "partition", "count", "getschema", "sample", "sample-distinct",
    "serialize", "mv-apply", "make-series",
}
_SOURCE_HEADS = {"source", "print", "let", "datatable", "externaldata", "range"}

_GRAMMAR = r"""
start: let_stmt* query_body
let_stmt: "let" NAME "=" let_rhs ";"
?let_rhs: table_expr | expr
?table_expr: datatable_expr | externaldata_expr | range_expr
?query_body: q_source | q_print | q_table
q_source: "source" ("|" operator)*
q_print: print_op ("|" operator)*
q_table: table_expr ("|" operator)*
print_op: "print" print_item ("," print_item)*
?print_item: NAME "=" expr -> print_named
           | expr           -> print_anon

datatable_expr: DATATABLE "(" col_decl ("," col_decl)* ")" "[" [expr ("," expr)* ","?] "]"
externaldata_expr: EXTERNALDATA "(" col_decl ("," col_decl)* ")" ed_uris ed_opts?
ed_uris: "[" ed_uri ("," ed_uri)* ","? "]"
ed_opts: "with" "(" ed_opt ("," ed_opt)* ")"
range_expr: "range" NAME "from" expr "to" expr "step" expr
col_decl: NAME ":" NAME
ed_uri: SQSTRING -> ed_uri
      | DQSTRING -> ed_uri
ed_opt: NAME "=" (SQSTRING|DQSTRING|NAME)

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
         | lookup_op
         | mvexpand_op
         | union_op
         | summarize_op
         | sort_op
         | top_op
         | distinct_op
         | take_op
         | join_op
         | as_op
         | fork_op
         | partition_op
         | getschema_op
         | count_op
         | sample_op
         | sampledistinct_op
         | serialize_op
         | mvapply_op
         | makeseries_op

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
lookup_op: LOOKUP lookup_kind? NAME "on" lookup_key ("," lookup_key)*
lookup_kind: "kind" "=" NAME
?lookup_key: NAME              -> lookup_key_same
           | NAME COMP NAME    -> lookup_key_map
join_op: "join" join_kind? join_src "on" lookup_key ("," lookup_key)*
join_kind: "kind" "=" NAME
join_src: "(" query_body ")"   -> join_sub
        | NAME                  -> join_tableref
as_op: "as" NAME
fork_op: "fork" fork_branch+
fork_branch: (NAME "=")? "(" operator ("|" operator)* ")"
partition_op: "partition" "by" NAME "(" operator ("|" operator)* ")"
getschema_op: "getschema"
count_op.-1: NAME
sample_op: "sample" NUMBER
sampledistinct_op: SAMPLE_DISTINCT NUMBER "of" NAME
serialize_op: "serialize" (assignment ("," assignment)*)?
mvapply_op: MVAPPLY mvapply_col ("," mvapply_col)* "on" "(" operator ("|" operator)* ")"
mvapply_col: NAME ("=" expr)?
makeseries_op: MAKESERIES (ms_agg ("," ms_agg)*)? "on" NAME _ms_range _ms_by?
_ms_range: ms_from? ms_to? ms_step
ms_from: "from" expr
ms_to: "to" expr
ms_step: "step" expr
_ms_by: "by" ms_key ("," ms_key)*
ms_agg: NAME "=" expr ("default" "=" expr)?
ms_key: NAME "=" expr    -> ms_key_named
      | expr             -> ms_key_anon
mvexpand_op: MVEXPAND mv_itemindex? mv_col ("," mv_col)* mv_limit?
mv_itemindex: "with_itemindex" "=" NAME
mv_col: NAME ("=" expr)?
mv_limit: "limit" NUMBER
union_op: UNION union_kind? union_src ("," union_src)*
union_kind: "kind" "=" NAME
union_src: "(" query_body ")"   -> union_sub
         | "source"              -> union_source_bare
         | NAME                  -> union_tableref
summarize_op: "summarize" (agg_item ("," agg_item)*)? ("by" by_item ("," by_item)*)?
agg_item: NAME "=" expr   -> agg_named
        | expr            -> agg_anon
by_item: NAME "=" expr    -> by_named
       | expr             -> by_anon
sort_op: ("sort"|"order") "by" sort_key ("," sort_key)*
sort_key: expr sort_dir?
sort_dir: "asc" -> asc_dir
        | "desc" -> desc_dir
top_op: "top" NUMBER "by" sort_key ("," sort_key)*
distinct_op: "distinct" NAME ("," NAME)*
take_op: ("take"|"limit") NUMBER
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
STROP.2: /!?(has_cs|has_any|has_all|has|contains_cs|contains)\b/
       | /!?(startswith_cs|startswith|endswith_cs|endswith|in)\b/
PROJECT_AWAY.2: /project-away\b/
PROJECT_KEEP.2: /project-keep\b/
PROJECT_REORDER.2: /project-reorder\b/
PROJECT_RENAME.2: /project-rename\b/
PARSE_KW.2: /parse-where\b|parse\b/
PARSEKV.3: /parse-kv\b/
DATATABLE.3: /datatable\b/
EXTERNALDATA.3: /externaldata\b/
LOOKUP.3: /lookup\b/
MVEXPAND.3: /mv-expand\b|mvexpand\b/
UNION.3: /union\b/
SAMPLE_DISTINCT.3: /sample-distinct\b/
MVAPPLY.3: /mv-apply\b/
MAKESERIES.3: /make-series\b/
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


def _auto_agg_name(call: Call) -> str:
    """Default output name for an anonymous summarize aggregate (KQL-style):
    ``count()`` -> ``count_``, ``sum(Price)`` -> ``sum_Price``."""
    if isinstance(call, Call) and call.args and isinstance(call.args[0], Column):
        return f"{call.name}_{call.args[0].name}"
    base = call.name if isinstance(call, Call) else "agg"
    return f"{base}_"


class _ToAst(Transformer):
    def start(self, ch: list) -> Query:
        lets = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and len(c) == 3 and c[0] == "let")
        tlets = tuple((c[1], c[2]) for c in ch
                      if isinstance(c, tuple) and len(c) == 3 and c[0] == "tlet")
        q = next(c for c in ch if isinstance(c, Query))
        return Query(operators=q.operators, lets=lets,
                     source_kind=q.source_kind, print_items=q.print_items,
                     table_lets=tlets, head_table=q.head_table)

    def let_stmt(self, ch: list) -> tuple:
        name, val = str(ch[0]), ch[1]
        if isinstance(val, (Datatable, ExternalData, Range)):
            return ("tlet", name, val)
        return ("let", name, val)

    def q_source(self, ch: list) -> Query:
        return Query(operators=tuple(ch), source_kind="source")

    def q_print(self, ch: list) -> Query:
        items = ch[0]
        ops = tuple(ch[1:])
        return Query(operators=ops, source_kind="print", print_items=tuple(items))

    def q_table(self, ch: list) -> Query:
        head = ch[0]
        ops = tuple(ch[1:])
        return Query(operators=ops, source_kind="table", head_table=head)

    # datatable / externaldata producers
    def col_decl(self, ch: list) -> tuple:
        return ("col", str(ch[0]), str(ch[1]))

    def datatable_expr(self, ch: list) -> Datatable:
        cols = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "col")
        values = tuple(c for c in ch if isinstance(c, Expr))
        return Datatable(cols, values)

    def ed_uri(self, ch: list) -> Literal:
        return Literal(_unescape(str(ch[0])))

    def ed_opt(self, ch: list) -> tuple:
        raw = ch[1]
        val = _unescape(str(raw)) if getattr(raw, "type", "") in ("SQSTRING", "DQSTRING") \
            else str(raw)
        return ("opt", str(ch[0]), val)

    def ed_uris(self, ch: list) -> tuple:
        return ("uris", tuple(c for c in ch if isinstance(c, Expr)))

    def ed_opts(self, ch: list) -> tuple:
        return ("opts", tuple((c[1], c[2]) for c in ch
                              if isinstance(c, tuple) and c and c[0] == "opt"))

    def externaldata_expr(self, ch: list) -> ExternalData:
        cols = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "col")
        uris: tuple = ()
        opts: tuple = ()
        for c in ch:
            if isinstance(c, tuple) and c and c[0] == "uris":
                uris = c[1]
            elif isinstance(c, tuple) and c and c[0] == "opts":
                opts = c[1]
        return ExternalData(cols, uris, opts)

    # lookup operator
    def lookup_kind(self, ch: list) -> tuple:
        return ("kind", str(ch[0]).lower())

    def lookup_key_same(self, ch: list) -> tuple:
        return ("key", str(ch[0]), str(ch[0]))

    def lookup_key_map(self, ch: list) -> tuple:
        if str(ch[1]) != "==":
            raise KqlCompileError("lookup 'on' supports only '==' key equality")
        return ("key", str(ch[0]), str(ch[2]))

    def lookup_op(self, ch: list) -> Lookup:
        kind = "leftouter"
        for c in ch:
            if isinstance(c, tuple) and c and c[0] == "kind":
                kind = c[1]
        table = next(str(t) for t in ch
                     if isinstance(t, Token) and t.type == "NAME")
        keys = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "key")
        return Lookup(table, keys, kind)

    # mv-expand operator
    def mv_itemindex(self, ch: list) -> tuple:
        return ("idx", str(ch[0]))

    def mv_col(self, ch: list) -> tuple:
        name = str(ch[0])
        expr = ch[1] if len(ch) > 1 else None
        return ("col", name, expr)

    def mv_limit(self, ch: list) -> tuple:
        return ("limit", int(float(str(ch[0]))))

    def mvexpand_op(self, ch: list) -> MvExpand:
        cols = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "col")
        idx = next((c[1] for c in ch
                    if isinstance(c, tuple) and c and c[0] == "idx"), None)
        lim = next((c[1] for c in ch
                    if isinstance(c, tuple) and c and c[0] == "limit"), None)
        return MvExpand(cols, idx, lim)

    # union operator
    def union_kind(self, ch: list) -> tuple:
        return ("kind", str(ch[0]).lower())

    def union_sub(self, ch: list) -> Query:
        return next(c for c in ch if isinstance(c, Query))

    def union_source_bare(self, ch: list) -> Query:
        return Query(source_kind="source")

    def union_tableref(self, ch: list) -> str:
        return str(ch[0])

    def union_op(self, ch: list) -> Union:
        kind = "outer"
        operands: list = []
        for c in ch:
            if isinstance(c, Token):
                continue                      # UNION keyword token
            if isinstance(c, tuple) and c and c[0] == "kind":
                kind = c[1]
            elif isinstance(c, (Query, str)):
                operands.append(c)
        return Union(tuple(operands), kind)

    # summarize operator
    def agg_named(self, ch: list) -> tuple:
        return ("agg", str(ch[0]), ch[1])

    def agg_anon(self, ch: list) -> tuple:
        return ("agg", None, ch[0])

    def by_named(self, ch: list) -> tuple:
        return ("by", str(ch[0]), ch[1])

    def by_anon(self, ch: list) -> tuple:
        return ("by", None, ch[0])

    def summarize_op(self, ch: list) -> Summarize:
        aggs: list[tuple[str, Call]] = []
        keys: list[tuple[str, Expr]] = []
        for c in ch:
            if not (isinstance(c, tuple) and c):
                continue
            if c[0] == "agg":
                name = c[1] or _auto_agg_name(c[2])
                aggs.append((name, c[2]))
            elif c[0] == "by":
                expr = c[2]
                name = c[1] or (expr.name if isinstance(expr, Column)
                                else f"Col{len(keys) + 1}")
                keys.append((name, expr))
        return Summarize(tuple(aggs), tuple(keys))

    # sort / order by, top
    def asc_dir(self, ch: list) -> bool:
        return False

    def desc_dir(self, ch: list) -> bool:
        return True

    def sort_key(self, ch: list) -> tuple:
        expr = ch[0]
        desc = ch[1] if len(ch) > 1 and isinstance(ch[1], bool) else True
        return (expr, desc)

    def sort_op(self, ch: list) -> Sort:
        keys = tuple(c for c in ch
                     if isinstance(c, tuple) and len(c) == 2 and isinstance(c[1], bool))
        return Sort(keys)

    def top_op(self, ch: list) -> Top:
        n = int(float(str(next(t for t in ch
                               if isinstance(t, Token) and t.type == "NUMBER"))))
        keys = tuple(c for c in ch
                     if isinstance(c, tuple) and len(c) == 2 and isinstance(c[1], bool))
        return Top(n, keys)

    def distinct_op(self, ch: list) -> Distinct:
        names = tuple(str(t) for t in ch
                      if isinstance(t, Token) and t.type == "NAME")
        return Distinct(names)

    def take_op(self, ch: list) -> Take:
        n = int(float(str(next(t for t in ch
                               if isinstance(t, Token) and t.type == "NUMBER"))))
        return Take(n)

    # join operator
    def join_kind(self, ch: list) -> tuple:
        return ("kind", str(ch[0]).lower())

    def join_sub(self, ch: list) -> Query:
        return next(c for c in ch if isinstance(c, Query))

    def join_tableref(self, ch: list) -> str:
        return str(ch[0])

    def join_op(self, ch: list) -> Join:
        kind = "innerunique"
        right: object = None
        keys: list[tuple[str, str]] = []
        for c in ch:
            if isinstance(c, Token):
                continue
            if isinstance(c, tuple) and c and c[0] == "kind":
                kind = c[1]
            elif isinstance(c, tuple) and c and c[0] == "key":
                keys.append((c[1], c[2]))
            elif isinstance(c, (Query, str)):
                right = c
        return Join(right, tuple(keys), kind)

    # as / fork / partition
    def as_op(self, ch: list) -> As:
        return As(str(ch[0]))

    def fork_branch(self, ch: list) -> tuple:
        name: str | None = None
        ops: list[Operator] = []
        for c in ch:
            if isinstance(c, Token) and c.type == "NAME":
                name = str(c)
            elif isinstance(c, Operator):
                ops.append(c)
        return ("branch", name, tuple(ops))

    def fork_op(self, ch: list) -> Fork:
        branches: list[tuple[str, tuple[Operator, ...]]] = []
        for c in ch:
            if isinstance(c, tuple) and c and c[0] == "branch":
                name = c[1] or f"Fork{len(branches) + 1}"
                branches.append((name, c[2]))
        return Fork(tuple(branches))

    def partition_op(self, ch: list) -> Partition:
        key = next(str(t) for t in ch
                   if isinstance(t, Token) and t.type == "NAME")
        ops = [c for c in ch if isinstance(c, Operator)]
        return Partition(key, tuple(ops))

    def getschema_op(self, ch: list) -> GetSchema:
        return GetSchema()

    def count_op(self, ch: list) -> Count:
        name = str(ch[0]).lower()
        if name != "count":
            raise KqlCompileError(f"unknown operator '{name}'")
        return Count()

    def sample_op(self, ch: list) -> Sample:
        n = int(float(str(next(t for t in ch
                               if isinstance(t, Token) and t.type == "NUMBER"))))
        return Sample(n)

    def sampledistinct_op(self, ch: list) -> SampleDistinct:
        n = int(float(str(next(t for t in ch
                               if isinstance(t, Token) and t.type == "NUMBER"))))
        col = next(str(t) for t in ch
                   if isinstance(t, Token) and t.type == "NAME")
        return SampleDistinct(n, col)

    def serialize_op(self, ch: list) -> Serialize:
        assigns = tuple(c for c in ch
                        if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], str))
        return Serialize(assigns)

    def mvapply_col(self, ch: list) -> tuple:
        name = str(ch[0])
        expr = ch[1] if len(ch) > 1 else None
        return ("mvcol", name, expr)

    def mvapply_op(self, ch: list) -> MvApply:
        cols = tuple((c[1], c[2]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "mvcol")
        ops = tuple(c for c in ch if isinstance(c, Operator))
        return MvApply(cols, ops)

    def ms_agg(self, ch: list) -> tuple:
        name = str(ch[0])
        exprs = [c for c in ch if isinstance(c, Expr)]
        default = exprs[1] if len(exprs) > 1 else None
        return ("agg", name, exprs[0], default)

    def ms_key_named(self, ch: list) -> tuple:
        return ("mskey", str(ch[0]), ch[1])

    def ms_key_anon(self, ch: list) -> tuple:
        expr = ch[0]
        return ("mskey", expr.name if isinstance(expr, Column) else None, expr)

    def ms_from(self, ch: list) -> tuple:
        return ("from", ch[0])

    def ms_to(self, ch: list) -> tuple:
        return ("to", ch[0])

    def ms_step(self, ch: list) -> tuple:
        return ("step", ch[0])

    def makeseries_op(self, ch: list) -> MakeSeries:
        aggs = tuple((c[1], c[2], c[3]) for c in ch
                     if isinstance(c, tuple) and c and c[0] == "agg")
        keys: list[tuple[str, Expr]] = []
        for c in ch:
            if isinstance(c, tuple) and c and c[0] == "mskey":
                keys.append((c[1] or f"Col{len(keys) + 1}", c[2]))
        axis = next(str(t) for t in ch
                    if isinstance(t, Token) and t.type == "NAME")
        start = next((c[1] for c in ch
                      if isinstance(c, tuple) and c and c[0] == "from"), None)
        stop = next((c[1] for c in ch
                     if isinstance(c, tuple) and c and c[0] == "to"), None)
        step = next(c[1] for c in ch
                    if isinstance(c, tuple) and c and c[0] == "step")
        return MakeSeries(aggs, axis, start, stop, step, tuple(keys))

    # range table producer
    def range_expr(self, ch: list) -> Range:
        name = next(str(t) for t in ch
                    if isinstance(t, Token) and t.type == "NAME")
        exprs = [c for c in ch if isinstance(c, Expr)]
        return Range(name, exprs[0], exprs[1], exprs[2])

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
    m0 = _OP_LEADING.match(first)
    head = m0.group(1).lower() if m0 else ""
    if head not in _SOURCE_HEADS:
        raise KqlCompileError(
            "a streaming-kql query must start with 'source', 'print', 'let', "
            "'datatable', or 'externaldata'")
    for seg in segs[1:]:
        m = _OP_LEADING.match(seg)
        if not m:
            raise KqlCompileError("expected an operator after '|'")
        name = m.group(1).lower()
        if name in _SUPPORTED_OPERATORS:
            continue
        if name in _DEFERRED_OPERATORS:
            raise KqlUnsupportedError(
                f"operator '{name}' is recognized but not yet implemented; it has a "
                "stateless per-record form and is planned")
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
