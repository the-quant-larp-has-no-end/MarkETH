"""Load demo data into the running stack (`make seed`).

    docker compose exec api python -m scripts.seed

A skeleton, like the services it depends on. Demo data has to be made the way real data
is made, through the services, so that every row it leaves behind is one the application
could have written: a signup grant with its journal entry, a market opened with its
caps checked and its audit row, trades with their outbox events. Writing the rows
directly would give a demo that shows states the application cannot reach. Until those
services exist there is nothing honest to load, and the command says so.

This module runs inside the api container. It must not import from `tests/`, which is
not part of the image.
"""

import asyncio
import sys

from app.container import Container
from app.core.config import get_settings
from app.core.logging import configure_logging

NOT_YET = (
    "scripts.seed: demo data is created through the application's services, which are "
    "not implemented yet. Nothing was written."
)


async def seed(container: Container) -> None:
    """Create the demo data once; a second run finds it and writes nothing.

    Everything goes through the services of `container`, as the user who would do it:

    1. People. For each address of `ADMIN_EMAILS` and `BARISTA_EMAILS` and three demo
       students (`demo1@…` to `demo3@…` on the first allowed domain): if
       `users.get_by_canonical_email` finds nobody, sign the address in with
       `AuthService.request_link` and `AuthService.verify`. The token comes from a
       mailer that keeps the mail instead of sending it (build the container with one;
       see `Container.build(settings, mailer=...)`). That creates the user, the
       account and the signup grant in one transaction. Throw the session away
       (`AuthService.logout`).
    2. Markets, proposed by a demo student with `MarketService.propose` and opened by
       the admin with `MarketLifecycleService.approve`: one per fixed tier, closing in
       7, 14 and 30 days. One more proposal is left PROPOSED and one is rejected, so
       the admin screens have something to show.
    3. Trades. Each demo student buys YES or NO for one of the stake presets in two of
       the markets (`TradingService.trade`, a fresh Idempotency-Key per trade), so
       prices have moved and charts have points.
    4. A voucher code batch of five codes (`VoucherCodeService.create_batch`). Print
       the codes to stdout: this is the only time they exist in the clear.

    Idempotent: step 1 skips who exists; steps 2 to 4 run only while no market exists
    at all. Stop at the first error and let it show; a half-seeded stack is repaired
    with `make reset`.
    """
    raise NotImplementedError


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.app.log_level)
    container = Container.build(settings)
    try:
        await seed(container)
    except NotImplementedError:
        sys.exit(NOT_YET)
    finally:
        await container.stop()


if __name__ == "__main__":
    asyncio.run(main())
