"""The media box: `python -m mediabox`, process 1 of its container (SPEC §9).

It reads its settings, wipes the HLS volume, removes a socket file left behind, listens,
and serves until the last broadcast it held is stopped or it is told to end (SIGTERM).
Either way the process ends with 0 and Compose starts the container again, empty.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import sys

from mediabox.box import Box
from mediabox.server import serve
from mediabox.settings import Settings, SettingsError

# What the server gets to finish the answers that are on their way when the process ends.
CLOSING_SECONDS = 1.0
# The signals that end the box: `docker stop`, and a person at a terminal.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def log(event: str, /, **fields: object) -> None:
    """One line per event on standard output. No byte of video, no name of a person."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)


def kill_everything_else() -> None:
    """Kills every process of the container but this one, which is its process 1."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(-1, signal.SIGKILL)


def wipe(settings: Settings) -> None:
    """Nothing of an earlier process is served: the HLS volume outlives a container."""
    settings.hls_root.mkdir(parents=True, exist_ok=True)
    for entry in settings.hls_root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                entry.unlink()


async def run(settings: Settings) -> None:
    wipe(settings)
    with contextlib.suppress(FileNotFoundError):
        settings.socket_path.unlink()
    # Only process 1 of a container may clear it out: anywhere else `kill(-1)` would
    # reach every process of the user.
    box = Box(settings, log=log, clear_out=kill_everything_else if os.getpid() == 1 else None)
    server = await serve(box, settings.socket_path)
    loop = asyncio.get_running_loop()
    shutting_down: set[asyncio.Task[None]] = set()

    def shut_down() -> None:
        shutting_down.add(loop.create_task(box.shut_down()))

    for number in STOP_SIGNALS:
        loop.add_signal_handler(number, shut_down)
    timers = asyncio.create_task(box.run_timers())
    try:
        log("listening", socket=str(settings.socket_path), capacity=settings.max_live)
        await box.finished.wait()
        server.close()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(CLOSING_SECONDS):
                await server.wait_closed()
        with contextlib.suppress(OSError):
            settings.socket_path.unlink()
    finally:
        # The handlers and the timers are this run's, and the loop can outlive it.
        timers.cancel()
        for number in STOP_SIGNALS:
            loop.remove_signal_handler(number)
    log("exit")


def main() -> int:
    try:
        settings = Settings.from_environment(os.environ)
    except SettingsError as error:
        print(error, file=sys.stderr)
        return 2
    asyncio.run(run(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
