"""Frozen contract loading and validation.

The kernel validates against the pinned `ai-stack` schemas themselves. No frozen
schema is restated in Python anywhere in this package.
"""

from __future__ import annotations

from .frozen import (
    APPROVAL,
    CAPABILITY_REQUEST,
    CAPABILITY_RESULT,
    ERROR,
    EVENT,
    EVIDENCE,
    EXTERNAL_GATE,
    RUNTIME_EXECUTION_REQUEST,
    RUNTIME_EXECUTION_RESULT,
    RUNTIME_ROUTE_DECISION,
    ContractsUnavailableError,
    FrozenContracts,
    StatusMapping,
    load_frozen_contracts,
)
from .jsonschema import SchemaViolation, UnsupportedSchemaError, Validator, schema_keywords

__all__ = [
    "APPROVAL",
    "CAPABILITY_REQUEST",
    "CAPABILITY_RESULT",
    "ERROR",
    "EVENT",
    "EVIDENCE",
    "EXTERNAL_GATE",
    "RUNTIME_EXECUTION_REQUEST",
    "RUNTIME_EXECUTION_RESULT",
    "RUNTIME_ROUTE_DECISION",
    "ContractsUnavailableError",
    "FrozenContracts",
    "SchemaViolation",
    "StatusMapping",
    "UnsupportedSchemaError",
    "Validator",
    "load_frozen_contracts",
    "schema_keywords",
]
