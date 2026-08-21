"""Frozen contract loading and validation.

The kernel validates against the pinned `ai-stack` schemas themselves. No frozen
schema is restated in Python anywhere in this package.
"""

from __future__ import annotations

from .frozen import (
    CAPABILITY_REQUEST,
    CAPABILITY_RESULT,
    ERROR,
    RUNTIME_EXECUTION_RESULT,
    ContractsUnavailableError,
    FrozenContracts,
    StatusMapping,
    load_frozen_contracts,
)
from .jsonschema import SchemaViolation, UnsupportedSchemaError, Validator, schema_keywords

__all__ = [
    "CAPABILITY_REQUEST",
    "CAPABILITY_RESULT",
    "ERROR",
    "RUNTIME_EXECUTION_RESULT",
    "ContractsUnavailableError",
    "FrozenContracts",
    "SchemaViolation",
    "StatusMapping",
    "UnsupportedSchemaError",
    "Validator",
    "load_frozen_contracts",
    "schema_keywords",
]
