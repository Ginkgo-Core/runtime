"""RK-09 -- ``rk-echo-transform``, the reference deterministic tool.

The concrete callable behind ``rk-reference-echo``. It exists to prove the
**boundary**, not the usefulness of the capability, and it is deliberately the
least interesting function in the repository:

.. code-block:: text

    input -> output

Zero of the following appear anywhere in it, per ``reference-slice.md`` section 2:

.. code-block:: text

    Amazon · Shopee · Power BI · Firecrawl · Browser Use · Playwright · Pix · Stripe
    any external API · any credential · any paid model · any network call · any container
    any filesystem write · any provider · any adapter

Its declared permissions are ``network: none``, ``filesystem_write: false``,
``shell: false``, ``external_side_effects: false`` -- and the tests assert those
by *execution*, blocking sockets and write-mode ``open`` while it runs, rather
than by reading the descriptor back.

The three entities that genuinely exist here are named honestly:
``system.echo.execute`` is the CAPABILITY, ``rk-reference-echo`` is the SKILL,
``rk-echo-transform`` is the TOOL. Adapter and provider do not exist and are
``null`` with a reason, which is the whole reason
``RuntimeSkillDescriptor`` supports honest nulls.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["TOOL_ID", "EchoInputError", "echo_transform"]

TOOL_ID = "rk-echo-transform"

#: The one field the tool reads. Everything else in ``input`` is carried through
#: untouched, because a diagnostic that silently dropped caller data would make
#: the boundary look cleaner than it is.
MESSAGE_FIELD = "message"

MAX_MESSAGE_LENGTH = 4096


class EchoInputError(ValueError):
    """The input is not something the tool can echo.

    A *tool* failure, not a boundary failure: RK-01 already validated the request
    against ``CapabilityRequest v1``, which declares ``input`` open, so a
    business-shaped defect surfaces here and becomes a ``failed`` execution with
    a normalised error -- never an exception crossing the boundary.
    """


def echo_transform(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the payload's message, plus what was observed about it.

    Pure. No I/O, no clock, no randomness, no global state.
    """
    if not isinstance(payload, Mapping):
        raise EchoInputError("input must be an object")

    message = payload.get(MESSAGE_FIELD)
    if not isinstance(message, str):
        raise EchoInputError("input.%s must be a string" % MESSAGE_FIELD)
    if not message:
        raise EchoInputError("input.%s must not be empty" % MESSAGE_FIELD)
    if len(message) > MAX_MESSAGE_LENGTH:
        raise EchoInputError(
            "input.%s is longer than %d characters" % (MESSAGE_FIELD, MAX_MESSAGE_LENGTH)
        )

    return {
        "echoed": message,
        "length": len(message),
        # Named so nobody mistakes this for a business capability that grew one.
        "tool_id": TOOL_ID,
    }
