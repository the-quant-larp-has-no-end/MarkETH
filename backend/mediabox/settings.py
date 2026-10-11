"""Settings of the media box (SPEC §9).

The box has no `.env`: Compose hands it these variables and no other. Each one is
required and held to its rule here, with no default; a missing or broken one ends the
process with a sentence that begins with the variable's name.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

# Where playlists and segments are written: the mount point of the HLS volume, which the
# proxy reads at the same path. A broadcast has the directory <HLS_ROOT>/<its id>.
HLS_ROOT: Final = Path("/var/lib/marketh/hls")

X264_PRESETS: Final = ("ultrafast", "superfast", "veryfast", "faster", "fast")

# The queue between a request and ffmpeg holds this many uploads of the largest size.
QUEUE_UPLOADS: Final = 4


class SettingsError(Exception):
    """A variable is missing or breaks its rule. The message begins with its name."""


@dataclass(frozen=True, slots=True)
class Settings:
    socket_path: Path
    hls_root: Path
    max_live: int
    max_minutes: int
    stall_seconds: int
    chunk_max_bytes: int
    input_max_width: int
    input_max_height: int
    video_height: int
    video_fps: int
    video_kbps: int
    audio_kbps: int
    hls_segment_seconds: int
    hls_playlist_segments: int
    ffmpeg_threads: int
    x264_preset: str

    @property
    def queue_max_bytes(self) -> int:
        """What one broadcast may have waiting for ffmpeg."""
        return QUEUE_UPLOADS * self.chunk_max_bytes

    @property
    def input_max_pixels(self) -> int:
        """The largest picture a decoder is allowed to make."""
        return self.input_max_width * self.input_max_height

    @property
    def keyframe_interval(self) -> int:
        """Frames from one key frame to the next: a segment begins with each."""
        return self.video_fps * self.hls_segment_seconds

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Self:
        read = _Reader(environment)
        socket_path = Path(read.text("BROADCAST_MEDIA_SOCKET"))
        if not socket_path.is_absolute():
            raise SettingsError("BROADCAST_MEDIA_SOCKET: must be an absolute path")
        preset = read.text("MEDIA_X264_PRESET")
        if preset not in X264_PRESETS:
            raise SettingsError(f"MEDIA_X264_PRESET: must be one of {', '.join(X264_PRESETS)}")
        return cls(
            socket_path=socket_path,
            hls_root=HLS_ROOT,
            max_live=read.whole("BROADCAST_MAX_LIVE", at_least=1),
            max_minutes=read.whole("BROADCAST_MAX_MINUTES", at_least=1),
            stall_seconds=read.whole("BROADCAST_STALL_SECONDS", at_least=10),
            chunk_max_bytes=read.whole("BROADCAST_CHUNK_MAX_BYTES", at_least=1024),
            input_max_width=read.whole("BROADCAST_INPUT_MAX_WIDTH", 640, 3840, even=True),
            input_max_height=read.whole("BROADCAST_INPUT_MAX_HEIGHT", 360, 2160, even=True),
            video_height=read.whole("BROADCAST_VIDEO_HEIGHT", 240, 1080, even=True),
            video_fps=read.whole("BROADCAST_VIDEO_FPS", 10, 30),
            video_kbps=read.whole("BROADCAST_VIDEO_KBPS", 200, 4000),
            audio_kbps=read.whole("BROADCAST_AUDIO_KBPS", 32, 192),
            hls_segment_seconds=read.whole("BROADCAST_HLS_SEGMENT_SECONDS", 1, 6),
            hls_playlist_segments=read.whole("BROADCAST_HLS_PLAYLIST_SEGMENTS", 3, 20),
            ffmpeg_threads=read.whole("MEDIA_FFMPEG_THREADS", 1, 8),
            x264_preset=preset,
        )


class _Reader:
    def __init__(self, environment: Mapping[str, str]) -> None:
        self._environment = environment

    def text(self, name: str) -> str:
        value = self._environment.get(name, "").strip()
        if not value:
            raise SettingsError(f"{name}: is not set")
        return value

    def whole(
        self,
        name: str,
        at_least: int,
        at_most: int | None = None,
        *,
        even: bool = False,
    ) -> int:
        text = self.text(name)
        if not text.isascii() or not text.isdigit():
            raise SettingsError(f"{name}: must be a whole number, not {text!r}")
        value = int(text)
        if value < at_least or (at_most is not None and value > at_most):
            span = f"at least {at_least}" if at_most is None else f"{at_least} to {at_most}"
            raise SettingsError(f"{name}: must be {span}, not {value}")
        if even and value % 2:
            raise SettingsError(f"{name}: must be even, not {value}")
        return value
