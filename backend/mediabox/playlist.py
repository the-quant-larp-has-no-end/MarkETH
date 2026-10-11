"""What a broadcast's playlist lists, and the files beside it that it does not list.

ffmpeg writes the playlist and deletes the segments that slide out of it. Two things it
leaves to the supervisor: saying when a playlist is long enough to be played, and the
files a process leaves behind when the next one takes the playlist over. A process only
knows the segments that are listed when it starts, so the ones its predecessor kept for
late players, and a half-written file of a predecessor that was killed, would stay for
the rest of the broadcast.
"""

from pathlib import Path

from mediabox.ffmpeg import PLAYLIST_NAME


def listed(directory: Path) -> list[str]:
    """The file names the playlist of this directory lists, in order. None: no playlist."""
    try:
        text = (directory / PLAYLIST_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def sweep(directory: Path) -> int:
    """Deletes every file the playlist does not list, and says how many there were.

    Called between two ffmpeg processes of one broadcast, when none is running.
    """
    keep = {PLAYLIST_NAME, *listed(directory)}
    removed = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if entry.name in keep:
            continue
        try:
            entry.unlink()
        except OSError:
            continue
        removed += 1
    return removed
