"""Exception hierarchy for streaming-kql."""
from __future__ import annotations


class KqlError(Exception):
    """Base class for all streaming-kql errors."""


class KqlCompileError(KqlError):
    """Raised when a query cannot be lexed, parsed, or semantically validated."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        self.line = line
        self.col = col
        where = ""
        if line is not None:
            where = f" (line {line}" + (f", col {col}" if col is not None else "") + ")"
        super().__init__(message + where)


class KqlUnsupportedError(KqlCompileError):
    """Raised at compile time for a recognized-but-unsupported feature.

    Notably every *stateful*/multi-record operator (summarize, join, sort, ...)
    is rejected here rather than silently ignored.
    """


class KqlEvalError(KqlError):
    """Raised at evaluation time when ``Options.strict_types`` is enabled and a
    value/type error occurs. In the default (null-tolerant) mode such situations
    yield null instead of raising."""
