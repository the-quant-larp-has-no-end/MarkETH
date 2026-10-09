"""Entry point of the relay process: `python -m app.relay` (SPEC §9).

Builds the same `Container` the API builds, runs the relay until SIGINT or SIGTERM, and
releases every connection on the way out. The relay sits above the container in the
layering, so this is where it is constructed.
"""

import asyncio
import logging
import signal

from app.container import Container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.relay.outbox_relay import OutboxRelay

logger = logging.getLogger("app.relay")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.app.log_level)
    container = Container.build(settings)
    relay = OutboxRelay(
        container.uow_factory, container.quote_history, settings.scylla, container.clock
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop_event.set)

    logger.info("Outbox relay started")
    try:
        await relay.run_forever(stop_event)
    finally:
        await container.stop()
        logger.info("Outbox relay stopped")


if __name__ == "__main__":
    asyncio.run(main())
