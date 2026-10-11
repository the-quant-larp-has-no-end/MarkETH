"""The adapter of the media box: the container that turns what a live stream uploads
into what its viewers play (SPEC §9).

The box decides nothing about who broadcasts or who watches. With the box down no live
stream starts or runs, and everything else works.
"""

from app.integrations.media.client import (
    BoxStatus,
    ChunkAnswer,
    ChunkRefusal,
    HeldBroadcast,
    MediaClient,
    MediaUnavailableError,
)

__all__ = [
    "BoxStatus",
    "ChunkAnswer",
    "ChunkRefusal",
    "HeldBroadcast",
    "MediaClient",
    "MediaUnavailableError",
]
