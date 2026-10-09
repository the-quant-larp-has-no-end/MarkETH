"""Stand-ins for a backend that is down, hanging or cut off, without stopping a container.

`closed_port` (tests/support/containers.py) is a backend that refuses connections.
`SilentServer` accepts them and never answers. `SwitchableProxy` forwards to a real
backend until a test cuts it, which is how a test lets ScyllaDB "die" and come back
while its data stays.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from tests.support.containers import LOOPBACK


@dataclass
class SilentServer:
    """A TCP server that accepts every connection and never answers."""

    port: int
    connections: int = 0


@asynccontextmanager
async def silent_server() -> AsyncIterator[SilentServer]:
    """A backend that hangs: clients connect, then wait until their own timeout."""
    state = SilentServer(port=0)

    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        state.connections += 1
        with suppress(ConnectionError):
            await reader.read()
        writer.close()

    server = await asyncio.start_server(swallow, LOOPBACK, 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()


class SwitchableProxy:
    """A TCP forwarder in front of a real backend that can be cut and restored.

    Cutting closes every open connection and leaves new ones unanswered, which is what
    a client sees when its backend's host goes away; restoring is the backend coming
    back. The backend itself keeps running, so a test needs no container restart and
    keeps its data.
    """

    def __init__(self, backend_host: str, backend_port: int) -> None:
        self._backend = (backend_host, backend_port)
        self._open: set[asyncio.StreamWriter] = set()
        self._is_cut = False
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._forward, LOOPBACK, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.close_clients()
            await self._server.wait_closed()

    def cut(self) -> None:
        self._is_cut = True
        for writer in self._open:
            writer.close()

    def restore(self) -> None:
        self._is_cut = False

    async def _forward(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._is_cut:
            with suppress(ConnectionError):
                await reader.read()
            writer.close()
            return
        try:
            backend_reader, backend_writer = await asyncio.open_connection(*self._backend)
        except OSError:
            writer.transport.abort()
            return
        pair = (writer, backend_writer)
        self._open.update(pair)

        async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            with suppress(ConnectionError):
                while chunk := await source.read(65536):
                    sink.write(chunk)
                    await sink.drain()
            # One direction ending ends the pair, which also stops the other pump.
            for end in pair:
                end.transport.abort()

        await asyncio.gather(pump(reader, backend_writer), pump(backend_reader, writer))
        self._open.difference_update(pair)
