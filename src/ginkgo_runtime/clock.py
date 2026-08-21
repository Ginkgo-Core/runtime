"""Time, as an input rather than an ambient fact.

Three frozen contracts require a timestamp: ``telemetry.started_at`` and
``ended_at`` on ``RuntimeExecutionResult``, ``collected_at`` on ``Evidence v1``,
and ``occurred_at`` on ``Event v1``. None of them may be invented, and none of
them may be read from the machine's clock inside a component that claims to be
reproducible.

So the clock is a **port**. The host supplies a real one; the E2E certificate
supplies :class:`SteppingClock`, which advances by a fixed amount per reading.

That is what makes the certificate mean something. A run whose timestamps come
from ``datetime.now()`` cannot be re-derived, so two runs of the same slice
produce two different evidence records and "deterministic evidence fingerprint"
is a claim nobody can check. With the clock as an input, the whole chain --
decision hash, execution result, evidence record, event stream -- is a pure
function of its inputs, and re-running it is a real verification rather than a
second opinion.

A stepping clock also keeps durations honest: a frozen clock would report every
execution as taking zero milliseconds, which is a number that looks like
telemetry and is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["RuntimeClock", "SteppingClock", "SystemClock"]


class RuntimeClock(Protocol):
    """One method. The kernel cannot ask the time in any other way."""

    def now(self) -> str:
        """An RFC 3339 timestamp."""
        ...


@dataclass
class SteppingClock:
    """Deterministic: starts at ``origin`` and advances ``step_ms`` per reading.

    Not a mock of a clock -- it is a clock whose readings are a function of how
    many times it has been read. Two runs that make the same calls in the same
    order see the same times.
    """

    origin: str = "2026-08-21T12:00:00+00:00"
    step_ms: int = 250
    _readings: int = field(default=0, repr=False)

    def now(self) -> str:
        moment = datetime.fromisoformat(self.origin.replace("Z", "+00:00")) + timedelta(
            milliseconds=self.step_ms * self._readings
        )
        self._readings += 1
        return moment.isoformat()

    @property
    def readings(self) -> int:
        return self._readings

    def reset(self) -> None:
        self._readings = 0


class SystemClock:
    """The wall clock, for a host that is not producing a reproducible artifact."""

    def now(self) -> str:  # pragma: no cover - not used by the certificate
        return datetime.now(UTC).isoformat()
