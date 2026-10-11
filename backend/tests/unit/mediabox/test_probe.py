"""The health check of the container: `python -m mediabox.probe`."""

import asyncio

import pytest

from mediabox import probe
from mediabox.box import Box
from tests.unit.mediabox.conftest import SOCKET


async def test_the_probe_passes_while_the_box_answers(
    listening: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BROADCAST_MEDIA_SOCKET", SOCKET)
    assert await asyncio.to_thread(probe.main) == 0


async def test_the_probe_fails_on_a_box_that_is_ending(
    listening: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BROADCAST_MEDIA_SOCKET", SOCKET)
    listening.ending = True
    try:
        assert await asyncio.to_thread(probe.main) == 1
    finally:
        listening.ending = False


@pytest.mark.parametrize("path", ["nobody-listens.sock", ""])
def test_the_probe_fails_without_a_socket(
    workspace: object, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("BROADCAST_MEDIA_SOCKET", path)
    assert probe.main() == 1
    monkeypatch.delenv("BROADCAST_MEDIA_SOCKET")
    assert probe.main() == 1
