"""streaming-kql — evaluate the stateless subset of KQL over a stream of events.

See docs/SPEC.md for the full specification.
"""
from __future__ import annotations

from .api import (
    KqlCompileError,
    KqlError,
    KqlEvalError,
    KqlUnsupportedError,
    Node,
    Options,
    Query,
    Schema,
    compile,
    function,
)

__version__ = "0.0.1"

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
    "__version__",
]
