"""Throwaway containers for the tests: which images, and how to find their ports.

The images are the ones `compose.yaml` runs, so the suite exercises what the stack does.
"""

import socket
import time
from typing import Final

from testcontainers.core.container import DockerContainer

POSTGRES_IMAGE: Final[str] = "postgres:18-alpine"
POSTGRES_PORT: Final[int] = 5432
# Durability is wasted on a database that lives for one test run.
POSTGRES_COMMAND: Final[str] = (
    "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
)

REDIS_IMAGE: Final[str] = "redis:8.8-alpine"
REDIS_PORT: Final[int] = 6379

SCYLLA_IMAGE: Final[str] = "scylladb/scylla:2026.1"
SCYLLA_PORT: Final[int] = 9042
# The single development node of SPEC §9.
SCYLLA_COMMAND: Final[str] = "--smp 1 --memory 750M --overprovisioned 1 --developer-mode 1"
SCYLLA_STARTUP_TIMEOUT_SECONDS: Final[int] = 300

MAILPIT_IMAGE: Final[str] = "axllent/mailpit:v1.31"
MAILPIT_SMTP_PORT: Final[int] = 1025
MAILPIT_HTTP_PORT: Final[int] = 8025

LOOPBACK: Final[str] = "127.0.0.1"

PORT_LOOKUP_SECONDS: Final[float] = 10.0


def published_port(container: DockerContainer, port: int) -> int:
    """The host port Docker mapped to a container port.

    Docker Desktop reports a container as running a moment before its port mappings
    exist, and Testcontainers raises `ConnectionError` for a lookup that comes too early.
    """
    deadline = time.monotonic() + PORT_LOOKUP_SECONDS
    while True:
        try:
            return container.get_exposed_port(port)
        except ConnectionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def closed_port() -> int:
    """A local port nothing listens on: connecting to it is refused at once."""
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        port: int = probe.getsockname()[1]
    return port
