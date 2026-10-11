"""What `.example.env` says to the media box and about its container.

The box has no `.env`: Compose hands it the variables of `BOX_VARIABLES` and no other
(SPEC §9). The tests of the box run on the values the template gives them.
"""

from pathlib import Path
from typing import Final

EXAMPLE_ENV_FILE: Final = Path(__file__).resolve().parents[4] / ".example.env"

BOX_VARIABLES: Final = (
    "BROADCAST_MEDIA_SOCKET",
    "BROADCAST_MAX_LIVE",
    "BROADCAST_MAX_MINUTES",
    "BROADCAST_STALL_SECONDS",
    "BROADCAST_CHUNK_MAX_BYTES",
    "BROADCAST_INPUT_MAX_WIDTH",
    "BROADCAST_INPUT_MAX_HEIGHT",
    "BROADCAST_VIDEO_HEIGHT",
    "BROADCAST_VIDEO_FPS",
    "BROADCAST_VIDEO_KBPS",
    "BROADCAST_AUDIO_KBPS",
    "BROADCAST_HLS_SEGMENT_SECONDS",
    "BROADCAST_HLS_PLAYLIST_SEGMENTS",
    "MEDIA_FFMPEG_THREADS",
    "MEDIA_X264_PRESET",
)


def example_environment() -> dict[str, str]:
    """Every variable of `.example.env` that is written without quotes, as text."""
    values: dict[str, str] = {}
    for line in EXAMPLE_ENV_FILE.read_text(encoding="utf-8").splitlines():
        name, assigned, value = line.partition("=")
        if assigned and not name.startswith("#") and not value.startswith(('"', "'")):
            values[name.strip()] = value.strip()
    return values


def box_environment() -> dict[str, str]:
    """The variables Compose hands the box, with the values of `.example.env`."""
    example = example_environment()
    return {name: example[name] for name in BOX_VARIABLES}
