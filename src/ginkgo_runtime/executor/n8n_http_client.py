"""RK-08B-B -- the socket. The only module in this package that has one.

Every other file in ``ginkgo_runtime`` is forbidden from importing a network
library, and ``tests/test_boundaries.py`` enforces that over the whole package
with **this file** as the single named exemption. Acquiring the ability to reach
a network anywhere else therefore requires editing that exemption list, in a
test, in a diff a reviewer reads -- which is the only version of "network access
exists only inside the concrete transport implementation" that survives contact
with maintenance.

The file is deliberately small and deliberately boring. Everything a live
transport does that is worth arguing about -- URL construction, credential
resolution, correlation propagation, workflow identity verification, structural
validation, failure normalisation, what may be written into evidence -- is in
:mod:`ginkgo_runtime.executor.n8n_transport`, which has no socket and is
therefore fully testable without one. What is left here is byte transfer and the
operating system's error table.

Two deadlines, not one
----------------------

``http.client`` rather than ``urllib.request``, for one reason:
:class:`~ginkgo_runtime.executor.n8n_transport.N8nEndpoint` carries a **connect**
deadline and a **request** deadline and they mean different things. A connection
that is never accepted is an n8n that is not running -- that should be reported in
a couple of seconds. A connection that is accepted and then answers slowly is a
workflow doing work, and it gets the longer bound. ``urllib.request`` exposes a
single ``timeout`` covering both, which would force one number to stand for two
questions. Here the connection is opened under the connect deadline and the
socket is re-armed with the request deadline immediately afterwards.

Nothing upstream is quoted, including by accident
-------------------------------------------------

Every ``except`` clause in this file discards the exception it caught. Not
wraps -- discards. ``OSError``'s string carries the operating system's message
and, for a proxy or a TLS failure, sometimes a URL with a userinfo component;
``http.client``'s carries whatever the remote wrote on the status line. The
failure classes this module raises take no message argument at all, so there is
nowhere for either to go.

The response body is returned as **bytes**, unparsed and untouched, and the
transport that receives it refuses to look at it at all unless the status was a
2xx. Reading a non-2xx body is the single richest leak path in a live bridge.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .n8n_transport import (
    MAX_RESPONSE_BYTES,
    HttpConnectionRefused,
    HttpRequest,
    HttpResponse,
    HttpTimeout,
    HttpTransportFailure,
)

__all__ = ["StdlibHttpClient"]


@dataclass
class StdlibHttpClient:
    """An :class:`~ginkgo_runtime.executor.n8n_transport.HttpClient` over ``http.client``.

    No session, no connection pool, no retry, no redirect following, no cookie
    jar and no proxy support. Each of those is a *decision*, and a decision taken
    inside an executor is exactly what the Executor Contract exists to prevent: a
    redirect is a re-route, a retry is a policy, and a cookie jar is state the
    kernel does not know it holds. One request, one connection, one response,
    closed.
    """

    #: TLS context for an ``https`` endpoint. Left ``None``, the stdlib default
    #: is used, which verifies the certificate and the hostname. Injectable so a
    #: deployment behind a private CA can supply its own trust store -- never so
    #: that verification can be turned off, which is why there is no
    #: ``verify: bool`` here to turn off.
    ssl_context: ssl.SSLContext | None = None

    def send(self, request: HttpRequest) -> HttpResponse:
        """Perform one exchange. Raises only the three declared failure classes."""
        parts = urlsplit(request.url)
        if parts.scheme not in ("http", "https"):
            raise HttpTransportFailure("unsupported scheme")
        host = parts.hostname
        if not host:
            raise HttpTransportFailure("no host")

        connect_timeout = max(0.001, request.connect_timeout_ms / 1000.0)
        request_timeout = max(0.001, request.request_timeout_ms / 1000.0)

        if parts.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, parts.port, timeout=connect_timeout, context=self.ssl_context
            )
        else:
            connection = http.client.HTTPConnection(
                host, parts.port, timeout=connect_timeout
            )

        # `failed` is set inside the handlers and raised outside them.
        #
        # `raise ... from None` clears `__cause__` and suppresses the printed
        # chain, but leaves the original hanging off `__context__`, where a
        # structured logger or an error reporter that walks the chain will find
        # it -- and the original is an `OSError` whose text comes from the
        # operating system, or an `HTTPException` whose text comes from the
        # remote's status line. Raising outside the handler is what makes the
        # word "discards" in this module's docstring literally true.
        failed: type[HttpTransportFailure] | None = None
        try:
            try:
                connection.connect()
            except (ConnectionRefusedError, socket.gaierror):
                # A refused connection and an unresolvable host are the same
                # operational fact for this bridge -- there is no n8n at the
                # configured endpoint -- and neither message is repeated.
                failed = HttpConnectionRefused
            except TimeoutError:
                failed = HttpTimeout
            except (OSError, ssl.SSLError):
                failed = HttpTransportFailure
            if failed is not None:
                raise failed()

            # The connect deadline is spent. Everything from here is the request
            # deadline, which is a different budget for a different question.
            if connection.sock is not None:
                connection.sock.settimeout(request_timeout)

            status, headers, body = 0, {}, b""
            try:
                connection.request(
                    request.method,
                    parts.path or "/",
                    body=request.body,
                    headers=dict(request.headers),
                )
                response = connection.getresponse()
                # `amt` bounds the read. `read()` with no bound would let a
                # misbehaving engine hand the kernel an unbounded body.
                body = response.read(min(request.max_response_bytes, MAX_RESPONSE_BYTES) + 1)
                status = int(response.status)
                # Header NAMES and values are carried across the port so that the
                # transport can be *shown* not to read them. Nothing here
                # inspects, filters or logs them.
                headers = {str(name): str(value) for name, value in response.getheaders()}
            except TimeoutError:
                failed = HttpTimeout
            except ConnectionRefusedError:
                failed = HttpConnectionRefused
            except (OSError, ssl.SSLError, http.client.HTTPException):
                failed = HttpTransportFailure
            if failed is not None:
                raise failed()
        finally:
            connection.close()

        return HttpResponse(status=status, headers=headers, body=body)
