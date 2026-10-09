"""Logging setup shared by the API and the relay."""

import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        force=True,
    )
    # While Scylla is down the driver logs a traceback per reconnection attempt at ERROR.
    # ScyllaClient reports the outage once itself, so the driver is silenced below CRITICAL.
    logging.getLogger("cassandra").setLevel(logging.CRITICAL)
