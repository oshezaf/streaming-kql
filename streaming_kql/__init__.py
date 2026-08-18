"""Evaluate KQL over independent events with per-event row sets and tables.

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

__version__ = "0.0.2"

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
