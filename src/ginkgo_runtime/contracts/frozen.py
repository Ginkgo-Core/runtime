"""The frozen contracts, loaded from the pinned snapshot. Never restated.

Everything the Capability Bus validates against is read out of the pinned
``ai-stack`` schemas at load time:

* ``CapabilityRequest v1`` -- ``contracts/core/capability-request.schema.json``
* ``CapabilityResult v1`` -- ``contracts/core/capability-result.schema.json``
* ``Error v1`` -- ``contracts/core/error.schema.json``
* ``RuntimeExecutionResult v1`` --
  ``contracts/runtime/runtime-execution-result.schema.json``
* the executor-state to result-status mapping -- the same schema's
  ``x-ginkgo-status-mapping``

The status mapping deserves the emphasis. The schema's own description says it:
*"The mapping between the two is declared in this schema under
x-ginkgo-status-mapping and is applied by the Capability Bus (RK-01), not by the
executor. Leaving that mapping undeclared is a known defect elsewhere in the
ecosystem."* So the Bus reads it rather than encoding a table of its own.

If any of it cannot be read, loading raises. A boundary that cannot read the
contract must never report that a request satisfies it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .jsonschema import Validator

__all__ = [
    "CAPABILITY_REQUEST",
    "CAPABILITY_RESULT",
    "ERROR",
    "RUNTIME_EXECUTION_RESULT",
    "ContractsUnavailableError",
    "FrozenContracts",
    "load_frozen_contracts",
]

CAPABILITY_REQUEST = "contracts/core/capability-request.schema.json"
CAPABILITY_RESULT = "contracts/core/capability-result.schema.json"
ERROR = "contracts/core/error.schema.json"
RUNTIME_EXECUTION_RESULT = "contracts/runtime/runtime-execution-result.schema.json"

STATUS_MAPPING_KEY = "x-ginkgo-status-mapping"

#: Executor states that are not terminal. They emit no ``CapabilityResult`` at all.
NON_TERMINAL_STATES = ("queued", "running")


class ContractsUnavailableError(RuntimeError):
    """A frozen contract could not be read. Fail closed; validate nothing."""


@dataclass(frozen=True)
class StatusMapping:
    """``RuntimeExecutionResult.state`` to ``CapabilityResult.status``, as declared.

    Read from ``x-ginkgo-status-mapping``; not a table maintained here.
    """

    by_state: Mapping[str, Mapping[str, Any]]

    def terminal_states(self) -> tuple[str, ...]:
        return tuple(sorted(self.by_state))

    def status_for(self, state: str, partial: bool) -> str:
        entry = self.by_state.get(state)
        if entry is None:
            raise ContractsUnavailableError(
                "the frozen status mapping declares no image for executor state %r; "
                "the Bus does not invent one" % state
            )
        if partial and entry.get("when_partial"):
            return str(entry["when_partial"])
        return str(entry["status"])

    def error_category_hint(self, state: str) -> str | None:
        entry = self.by_state.get(state) or {}
        hint = entry.get("error_category")
        return str(hint) if isinstance(hint, str) else None


@dataclass(frozen=True)
class FrozenContracts:
    """Validators and declared values for the contracts the Bus touches."""

    capability_request: Validator
    capability_result: Validator
    error: Validator
    runtime_execution_result: Validator
    status_mapping: StatusMapping
    #: ``capability.name`` pattern, read from the frozen request schema and never
    #: written as a literal (``capability-runtime-kernel-v1.md`` section 5).
    capability_name_pattern: str
    #: The one ``contract_version`` this kernel implements, read from the schema's
    #: own ``x-ginkgo-contract-version``. A request naming any other version is
    #: rejected -- see reference-slice N13.
    supported_contract_version: str
    #: ``error.message`` ``maxLength``, read from the frozen contract. The Bus
    #: clamps to it rather than hard-coding 2048, and rather than discovering the
    #: limit by emitting an invalid error.
    error_message_max_length: int


def _dig(document: Any, path: tuple[str, ...], origin: str) -> Any:
    cursor: Any = document
    for step in path:
        if not isinstance(cursor, dict) or step not in cursor:
            raise ContractsUnavailableError(
                "%s does not carry %s; the frozen value cannot be read and the "
                "Bus refuses to substitute a literal" % (origin, ".".join(path))
            )
        cursor = cursor[step]
    return cursor


def load_frozen_contracts(read_source: Callable[[str], bytes]) -> FrozenContracts:
    """Load every frozen contract through ``read_source``.

    ``read_source`` is the manifest-backed reader, so each schema is pinned by
    commit SHA and integrity-checked exactly like the catalog's sources. Nothing
    here touches the network or an unpinned file.
    """
    schemas: dict[str, Any] = {}
    for path in (CAPABILITY_REQUEST, CAPABILITY_RESULT, ERROR, RUNTIME_EXECUTION_RESULT):
        try:
            schemas[path] = json.loads(read_source(path).decode("utf-8"))
        except Exception as exc:
            raise ContractsUnavailableError(
                "frozen contract %s could not be read: %s" % (path, exc)
            ) from exc

    def resolver(ref: str) -> Any:
        # The frozen contracts use one relative ref: `./error.schema.json` from
        # `capability-result.schema.json`. Resolution goes through the pinned set,
        # never the filesystem, so a ref cannot escape the snapshot.
        # `str.lstrip` strips CHARACTERS, not a prefix: "../core/error.schema.json"
        # .lstrip("./") yields "core/error.schema.json", which matches no basename.
        # Compare basenames instead.
        name = ref.rsplit("/", 1)[-1]
        for path, schema in schemas.items():
            if path.rsplit("/", 1)[-1] == name:
                return schema
        raise ContractsUnavailableError(
            "$ref %r does not resolve to a pinned contract; the Bus does not "
            "fetch a schema it was not given" % ref
        )

    mapping = _dig(
        schemas[RUNTIME_EXECUTION_RESULT], (STATUS_MAPPING_KEY,), RUNTIME_EXECUTION_RESULT
    )
    by_state = {
        state: entry
        for state, entry in mapping.items()
        if isinstance(entry, dict) and "status" in entry
    }
    if not by_state:
        raise ContractsUnavailableError(
            "%s declares no usable %s; RK-01 will not invent the executor-state to "
            "result-status mapping" % (RUNTIME_EXECUTION_RESULT, STATUS_MAPPING_KEY)
        )

    return FrozenContracts(
        capability_request=Validator(schemas[CAPABILITY_REQUEST], resolver),
        capability_result=Validator(schemas[CAPABILITY_RESULT], resolver),
        error=Validator(schemas[ERROR], resolver),
        runtime_execution_result=Validator(schemas[RUNTIME_EXECUTION_RESULT], resolver),
        status_mapping=StatusMapping(by_state=by_state),
        capability_name_pattern=_dig(
            schemas[CAPABILITY_REQUEST],
            ("properties", "capability", "properties", "name", "pattern"),
            CAPABILITY_REQUEST,
        ),
        supported_contract_version=str(
            _dig(schemas[CAPABILITY_REQUEST], ("x-ginkgo-contract-version",), CAPABILITY_REQUEST)
        ),
        error_message_max_length=int(
            _dig(schemas[ERROR], ("properties", "message", "maxLength"), ERROR)
        ),
    )
