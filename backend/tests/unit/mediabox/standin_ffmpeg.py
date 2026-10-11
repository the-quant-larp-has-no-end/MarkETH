#!/usr/bin/env python3
"""A stand-in for ffmpeg in the unit tests of the media box.

The tests put a copy of this file first on the PATH under the name `ffmpeg`. It reads its
standard input and writes a playlist and segment files where the real command line says,
one segment per SEGMENT_BYTES bytes read, so that the supervisor can be tested without a
codec. What it was started with and how many bytes it read is kept beside it:

    runs.jsonl          one line when it starts ({"pid", "argv"}), one when it ends
    bytes-<pid>         the bytes read so far, rewritten after every read

Words in the input make it misbehave: EXIT3 (end with code 3 at once), HANG (stop
reading, ignore SIGTERM and a closed input), STOPREADING (stop reading, stay alive).
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

SEGMENT_BYTES = 1000
HERE = Path(__file__).resolve().parent


def note(record: dict[str, object]) -> None:
    with (HERE / "runs.jsonl").open("a") as log:
        log.write(json.dumps({"pid": os.getpid(), **record}) + "\n")


def main() -> int:
    argv = sys.argv
    playlist = Path(argv[-1])
    pattern = argv[argv.index("-hls_segment_filename") + 1]
    list_size = int(argv[argv.index("-hls_list_size") + 1])
    continues = "append_list" in argv[argv.index("-hls_flags") + 1]
    note({"event": "start", "argv": argv})

    entries: list[tuple[str, bool]] = []  # (file name, begins after a break)
    number = 0
    first = 0
    if continues and playlist.exists():
        lines = playlist.read_text().splitlines()
        for line in lines:
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                first = int(line.split(":")[1])
        names = [line for line in lines if line and not line.startswith("#")]
        entries = [(name, False) for name in names]
        number = first + len(names)
    pending_break = continues

    def write_playlist() -> None:
        nonlocal first
        while len(entries) > list_size:
            name, _ = entries.pop(0)
            first += 1
            (playlist.parent / name).unlink(missing_ok=True)
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:6",
            "#EXT-X-TARGETDURATION:2",
            f"#EXT-X-MEDIA-SEQUENCE:{first}",
        ]
        for name, broken in entries:
            if broken:
                lines.append("#EXT-X-DISCONTINUITY")
            lines += ["#EXTINF:2.000000,", name]
        temporary = playlist.with_suffix(".m3u8.tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.replace(playlist)

    total = 0
    unwritten = 0
    while True:
        data = os.read(0, 65536)
        if not data:
            break
        total += len(data)
        unwritten += len(data)
        (HERE / f"bytes-{os.getpid()}").write_text(str(total))
        if b"EXIT3" in data:
            print("stand-in: asked to fail", file=sys.stderr, flush=True)
            note({"event": "end", "bytes": total, "why": "EXIT3"})
            return 3
        if b"HANG" in data:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            note({"event": "hang", "bytes": total})
            while True:
                time.sleep(3600)
        if b"STOPREADING" in data:
            note({"event": "stopped reading", "bytes": total})
            signal.pause()
        while unwritten >= SEGMENT_BYTES:
            unwritten -= SEGMENT_BYTES
            name = Path(pattern % number).name
            (playlist.parent / name).write_bytes(b"segment")
            entries.append((name, pending_break))
            pending_break = False
            number += 1
            write_playlist()
    note({"event": "end", "bytes": total, "why": "end of input"})
    return 0


sys.exit(main())
