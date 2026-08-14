"""Public API for streaming-kql."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from . import functions as _functions
from .errors import (
    KqlCompileError,
    KqlError,
    KqlEvalError,
    KqlUnsupportedError,
)
from .evaluator import CompiledQuery, Options, Record
from .parser import parse

__all__ = [
    "compile",
    "Query",
    "Node",
    "Schema",
    "Options",
    "function",
    "KqlError",
    "KqlCompileError",
    "KqlUnsupportedError",
    "KqlEvalError",
]


class Schema:
    """Optional input column typing (``column -> KQL type``).

    When supplied, each declared column of an input record is **coerced** to its
    KQL type before the query runs (e.g. an ISO-8601 ``string`` becomes a
    tz-aware ``datetime``, a JSON ``string`` becomes a ``dynamic`` object). A
    value that cannot be converted becomes null. Undeclared columns are left as
    supplied. See docs/SPEC.md §3.2 for the type mapping.
    """

    def __init__(self, columns: dict[str, str] | None = None):
        self.columns = dict(columns or {})


class Query:
    """A compiled, reusable streaming-KQL query."""

    def __init__(self, source: str, schema: Schema | None, options: Options | None):
        self.source = source
        self.schema = schema
        self.options = options or Options()
        self._compiled = CompiledQuery(
            parse(source), self.options,
            schema.columns if schema else None)

    def transform(self, record: Record) -> list[Record]:
        return self._compiled.transform(record)

    def match(self, record: Record) -> Record | None:
        return self._compiled.match(record)

    def stream(self, records: Iterable[Record]) -> Iterator[Record]:
        return self._compiled.stream(records)


def compile(  # noqa: A001 - deliberately mirrors KQL/`re.compile` ergonomics
    source: str,
    schema: Schema | None = None,
    options: Options | None = None,
) -> Query:
    """Parse and validate *source* once, returning a reusable :class:`Query`."""
    return Query(source, schema, options)


class Node:
    """Host that runs many standing queries over one feed (Rx.KQL ``KqlNode``)."""

    def __init__(self, options: Options | None = None):
        self._options = options
        self._queries: dict[str, Query] = {}

    def add(self, name: str, source: str, schema: Schema | None = None) -> None:
        self._queries[name] = compile(source, schema, self._options)

    def remove(self, name: str) -> None:
        self._queries.pop(name, None)

    def push(self, record: Record) -> Iterator[tuple[str, Record]]:
        """Offer *record* to every query; yield ``(query_name, out_record)``."""
        for name, q in self._queries.items():
            for out in q.transform(record):
                yield name, out


def function(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a custom scalar function usable inside KQL expressions."""
    return _functions.register(name)
