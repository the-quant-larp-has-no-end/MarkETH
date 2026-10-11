"""The ffmpeg command of one part (SPEC §9). Pure: no I/O.

One command for every input: whatever the browser recorded is decoded and encoded again
as H.264 and AAC, in MPEG-TS segments of BROADCAST_HLS_SEGMENT_SECONDS, listed in a
sliding playlist. Each part is one ffmpeg process that reads the part from its standard
input; a later part's process continues the playlist of the one before.
"""

import re
from pathlib import Path
from typing import Final

from mediabox.settings import Settings
from mediabox.sniff import Container

PLAYLIST_NAME: Final = "index.m3u8"
SEGMENT_PATTERN: Final = "seg-%06d.ts"

# The decoders ffmpeg may use, by ffmpeg's own names: what Chrome, Firefox and Safari
# record (VP8 or H.264 with Opus or AAC). Named, because the build has some five hundred.
DECODERS: Final = ("vp8", "h264", "opus", "aac")

# Segments kept on disk after they left the playlist, for a player that asks late.
KEPT_AFTER_PLAYLIST: Final = 2

_BROADCAST_ID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def is_broadcast_id(text: str) -> bool:
    """Whether a text is a UUID as the proxy accepts one in an address."""
    return _BROADCAST_ID.fullmatch(text) is not None


def directory(settings: Settings, broadcast_id: str) -> Path:
    """Where a broadcast's playlist and segments lie. The id is checked before it is a path."""
    if not is_broadcast_id(broadcast_id):
        raise ValueError("not a broadcast id")
    return settings.hls_root / broadcast_id


# What every process asks of the HLS muxer: a sliding playlist whose segments are deleted
# once they slid out of it, files that appear whole (written under another name, then
# renamed), a playlist that never says the stream has ended, and the wall-clock time of
# every segment.
_HLS_FLAGS: Final = "delete_segments+omit_endlist+temp_file+independent_segments+program_date_time"


def arguments(
    settings: Settings, target: Path, container: Container, *, continues: bool
) -> list[str]:
    """The command that turns one part, read from standard input, into HLS.

    `target` is the broadcast's directory, as `directory()` gives it.

    `continues` is true when the broadcast has a playlist already: the process then reads
    it, goes on with its numbers and marks the break (`append_list` writes
    `#EXT-X-DISCONTINUITY` before its first segment). The first process of a broadcast
    gets neither that flag nor `discont_start`: each of the two would open the playlist
    with a break that is none.
    """
    video_rate = f"{settings.video_kbps}k"
    scale = f"scale=-2:'min({settings.video_height},ih)'"
    options: list[tuple[str, ...]] = [
        ("ffmpeg", "-hide_banner"),
        ("-loglevel", "error"),
        ("-nostats",),
        # The input: named, bounded, and read from the pipe alone. Each of these stands
        # before `-i`; after it, it would be an option of the output.
        ("-fflags", "+genpts+discardcorrupt"),
        ("-probesize", "500000"),
        ("-analyzeduration", "1000000"),
        ("-max_pixels", str(settings.input_max_pixels)),
        ("-protocol_whitelist", "pipe"),
        ("-codec_whitelist", ",".join(DECODERS)),
        ("-f", container),
        ("-i", "pipe:0"),
        # One picture and, where there is one, one sound.
        ("-map", "0:v:0"),
        ("-map", "0:a:0?"),
        ("-vf", f"{scale},fps={settings.video_fps},format=yuv420p"),
        ("-c:v", "libx264"),
        ("-preset", settings.x264_preset),
        ("-profile:v", "main"),
        ("-threads", str(settings.ffmpeg_threads)),
        ("-b:v", video_rate),
        ("-maxrate", video_rate),
        ("-bufsize", f"{2 * settings.video_kbps}k"),
        ("-g", str(settings.keyframe_interval)),
        ("-keyint_min", str(settings.keyframe_interval)),
        ("-sc_threshold", "0"),
        ("-c:a", "aac"),
        ("-b:a", f"{settings.audio_kbps}k"),
        ("-ar", "48000"),
        ("-ac", "2"),
        ("-af", "aresample=async=1000"),
        # HLS: a sliding playlist that a later process continues.
        ("-f", "hls"),
        ("-hls_time", str(settings.hls_segment_seconds)),
        ("-hls_list_size", str(settings.hls_playlist_segments)),
        ("-hls_delete_threshold", str(KEPT_AFTER_PLAYLIST)),
        ("-hls_segment_type", "mpegts"),
        ("-hls_flags", f"{_HLS_FLAGS}+append_list" if continues else _HLS_FLAGS),
        ("-hls_segment_filename", str(target / SEGMENT_PATTERN)),
        (str(target / PLAYLIST_NAME),),
    ]
    return [word for option in options for word in option]
