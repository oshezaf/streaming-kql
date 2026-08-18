"""Abstract syntax tree node definitions.

The AST is split into *tabular operators* (each transforms a record into zero or
more records) and *scalar expressions* (each computes a value from a record's
environment). Keeping the two cleanly separated is what will let a future
stateful extension add multi-record operators without touching scalar
evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# --- scalar expressions ------------------------------------------------------
class Expr:
    """Base class for scalar expression nodes."""


@dataclass(frozen=True)
class Literal(Expr):
    value: object


@dataclass(frozen=True)
class Column(Expr):
    name: str


@dataclass(frozen=True)
class Unary(Expr):
    op: str
    operand: Expr


@dataclass(frozen=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Call(Expr):
    name: str
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class Member(Expr):
    """Dynamic property access: ``target.name``."""

    target: Expr
    name: str


@dataclass(frozen=True)
class Index(Expr):
    """Dynamic index access: ``target[key]``."""

    target: Expr
    key: Expr


@dataclass(frozen=True)
class ExprList(Expr):
    """A parenthesized list, used as the right-hand side of ``in``/``!in``."""

    items: tuple[Expr, ...]


# --- tabular operators -------------------------------------------------------
class Operator:
    """Base class for tabular operator nodes."""


@dataclass(frozen=True)
class Where(Operator):
    predicate: Expr


@dataclass(frozen=True)
class Extend(Operator):
    assignments: tuple[tuple[str, Expr], ...]


@dataclass(frozen=True)
class ProjectItem:
    name: str
    expr: Expr | None  # None => keep the existing column ``name``


@dataclass(frozen=True)
class Project(Operator):
    items: tuple[ProjectItem, ...]


@dataclass(frozen=True)
class ProjectAway(Operator):
    names: tuple[str, ...]


@dataclass(frozen=True)
class ProjectKeep(Operator):
    names: tuple[str, ...]


@dataclass(frozen=True)
class ProjectRename(Operator):
    pairs: tuple[tuple[str, str], ...]  # (new_name, old_name)


@dataclass(frozen=True)
class ParseSeg:
    """One segment of a ``parse`` pattern: a literal, a wildcard, or a column."""

    kind: str                 # 'lit' | 'star' | 'col'
    value: str = ""           # literal text (lit) or column name (col)
    col_type: str | None = None  # optional KQL type for a column segment


@dataclass(frozen=True)
class Parse(Operator):
    source: Expr
    kind: str                 # 'simple' | 'regex' | 'relaxed'
    segments: tuple[ParseSeg, ...]
    drop_unmatched: bool = False  # True for ``parse-where`` (drop non-matching rows)


@dataclass(frozen=True)
class ProjectReorder(Operator):
    names: tuple[str, ...]


@dataclass(frozen=True)
class ParseKv(Operator):
    source: Expr
    columns: tuple[tuple[str, str | None], ...]  # (name, type)
    options: tuple[tuple[str, str], ...]          # (option_name, value)


@dataclass(frozen=True)
class BagUnpack(Operator):
    column: str
    prefix: str = ""


@dataclass(frozen=True)
class Lookup(Operator):
    """Enrich each record from a constant reference table (a ``let`` bound to a
    ``datatable``/``externaldata``). Stateless: the table is fixed at compile time
    and does not depend on the stream. At most one matching row is joined."""

    table: str
    keys: tuple[tuple[str, str], ...]   # (left_column, right_column)
    kind: str = "leftouter"             # 'leftouter' | 'inner'


@dataclass(frozen=True)
class MvExpand(Operator):
    """Expand array/bag column(s) into rows (1 → N). Multiple columns expand in
    lockstep (zipped), padded to the longest with null. Each entry is
    ``(out_name, source_expr)``; ``source_expr`` is ``None`` to expand the
    existing column ``out_name`` in place, or an expression for the
    ``NewCol = <expr>`` form."""

    columns: tuple[tuple[str, Expr | None], ...]
    item_index: str | None = None       # with_itemindex=Name
    limit: int | None = None            # cap elements per input row


@dataclass(frozen=True)
class Union(Operator):
    """Concatenate the incoming stream with additional table expressions,
    evaluated per record. Operands are constant reference-table names or
    ``source``-based subqueries (Query nodes) — the stateless slice of union."""

    operands: tuple[object, ...]        # each is a str (table ref) or a Query
    kind: str = "outer"                 # 'outer' (null-fill) | 'inner' (common cols)


# --- batch (per-record row-set) operators ------------------------------------
# These reduce/reorder the set of rows produced *from a single input record*
# (e.g. after mv-expand/union). They are stateless because they never cross
# input records — see docs/SPEC.md §2.4.
@dataclass(frozen=True)
class Summarize(Operator):
    aggregates: tuple[tuple[str, Call], ...]      # (out_name, aggregate call)
    by_keys: tuple[tuple[str, Expr], ...]          # (out_name, grouping expr)


@dataclass(frozen=True)
class Sort(Operator):
    keys: tuple[tuple[Expr, bool], ...]            # (key expr, descending?)


@dataclass(frozen=True)
class Top(Operator):
    count: int
    keys: tuple[tuple[Expr, bool], ...]            # (key expr, descending?)


@dataclass(frozen=True)
class Distinct(Operator):
    columns: tuple[str, ...]


@dataclass(frozen=True)
class Take(Operator):
    count: int


@dataclass(frozen=True)
class Join(Operator):
    """Join the per-record row-set (left) with a bounded right table — a
    ``source`` subquery re-derived from the same input record, or a constant
    reference table. Stateless: both sides are fully materialised in memory for
    the one record being processed, so every join *kind* (including right/full
    outer) is computable without cross-record state. See docs/SPEC.md §2.4."""

    right: object                        # str (table ref) or Query (subquery)
    keys: tuple[tuple[str, str], ...]    # (left_column, right_column)
    kind: str = "innerunique"


@dataclass(frozen=True)
class As(Operator):
    """Name the current per-record row-set so later operators (``join``/``union``)
    can reference it as a table within the same record's processing."""

    name: str


@dataclass(frozen=True)
class Fork(Operator):
    """Run several sub-pipelines on the current per-record row-set, capturing
    each result as a named table (for later ``join``/``union``). The input row-set
    passes through unchanged."""

    branches: tuple[tuple[str, tuple[Operator, ...]], ...]   # (name, sub-operators)


@dataclass(frozen=True)
class Case(Operator):
    """Route each row through the first sub-pipeline whose predicate is true;
    unmatched rows run through the required default sub-pipeline."""

    branches: tuple[tuple[Expr, tuple[Operator, ...]], ...]
    default: tuple[Operator, ...]


@dataclass(frozen=True)
class Partition(Operator):
    """Group the current per-record row-set by a column and run a sub-pipeline on
    each group, concatenating the results (1 → N, stateless within the record)."""

    key: str
    operators: tuple[Operator, ...]


@dataclass(frozen=True)
class Count(Operator):
    """Count the rows of the per-record row-set, emitting a single ``Count`` row."""


@dataclass(frozen=True)
class GetSchema(Operator):
    """Describe the columns of the per-record row-set as rows
    (``ColumnName``, ``ColumnOrdinal``, ``ColumnType``)."""


@dataclass(frozen=True)
class Sample(Operator):
    """Return up to ``count`` randomly-chosen rows of the per-record row-set."""

    count: int


@dataclass(frozen=True)
class SampleDistinct(Operator):
    """Return up to ``count`` random distinct values of ``column`` (one column)."""

    count: int
    column: str


@dataclass(frozen=True)
class Serialize(Operator):
    """Assign columns over the ordered per-record row-set, allowing **window
    functions** (``row_number``, ``prev``, ``next``, ``row_cumsum``) that depend
    on a row's position. With no assignments it is the identity (the row-set is
    already an ordered list)."""

    assignments: tuple[tuple[str, Expr], ...]


@dataclass(frozen=True)
class MvApply(Operator):
    """For each row, expand array column(s) into a subtable, run a sub-pipeline on
    it, and combine each result with the row's remaining columns (1 → N)."""

    columns: tuple[tuple[str, Expr | None], ...]   # (subtable_col, source_expr)
    operators: tuple[Operator, ...]


@dataclass(frozen=True)
class MakeSeries(Operator):
    """Bin ``axis`` into intervals and produce, per group, one row whose aggregate
    columns are **arrays** (one value per bin) plus the axis as an array of bin
    starts. ``start``/``stop`` are optional — when omitted they are inferred from
    the row-set's axis min/max (aligned to ``step``). Stateless over the
    per-record row-set."""

    aggregates: tuple[tuple[str, Call, Expr | None], ...]  # (name, agg call, default)
    axis: str
    start: Expr | None
    stop: Expr | None
    step: Expr
    by_keys: tuple[tuple[str, Expr], ...]






# --- tabular producers (constant reference tables) ---------------------------
@dataclass(frozen=True)
class Datatable:
    """An inline constant table: ``datatable(Col:type, ...) [ v, v, ... ]``."""

    columns: tuple[tuple[str, str], ...]   # (name, kql_type)
    values: tuple[Expr, ...]               # flat, row-major scalar expressions


@dataclass(frozen=True)
class ExternalData:
    """A constant table read from local file(s):
    ``externaldata(Col:type, ...) [ "path" ] with (format=...)``."""

    columns: tuple[tuple[str, str], ...]
    uris: tuple[Expr, ...]
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Range:
    """A generated single-column table:
    ``range Name from Start to Stop step Step`` (constant, materialised once)."""

    name: str
    start: Expr
    stop: Expr
    step: Expr



@dataclass(frozen=True)
class Query:
    operators: tuple[Operator, ...] = field(default_factory=tuple)
    lets: tuple[tuple[str, Expr], ...] = ()
    source_kind: str = "source"                   # 'source' | 'print' | 'table'
    print_items: tuple[tuple[str | None, Expr], ...] = ()
    table_lets: tuple[tuple[str, object], ...] = ()   # (name, Datatable|ExternalData)
    # Datatable|ExternalData producer when source_kind == 'table'
    head_table: object | None = None



