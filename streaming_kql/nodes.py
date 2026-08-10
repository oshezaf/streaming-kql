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
class ProjectRename(Operator):
    pairs: tuple[tuple[str, str], ...]  # (new_name, old_name)


@dataclass(frozen=True)
class Query:
    operators: tuple[Operator, ...] = field(default_factory=tuple)
