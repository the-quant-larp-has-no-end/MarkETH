"""What the image test does beside the box, inside a container of its own.

`tests/integration/test_mediabox_image.py` starts this file with the box's image (it needs
Python and ffprobe), as the API's user with the socket volume and the HLS volume
read-only: the place the API and the proxy have. It feeds one fixture of this directory
through the socket and prints one JSON object of what it saw.

    python beside_the_box.py <fixture> <socket> <HLS directory>
"""

import contextlib
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent
SOCKET = sys.argv[2]
HLS = Path(sys.argv[3])

# How long the box has to list what it was fed.
LISTING_SECONDS = 30
# Pieces go in faster than they were recorded.
SECONDS_PER_PIECE = 0.3


def ask(request: dict[str, Any], body: bytes = b"") -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(SOCKET)
        connection.sendall(json.dumps(request).encode() + b"\n" + body)
        answer: dict[str, Any] = json.loads(connection.makefile("rb").readline())
        return answer


def chunk(broadcast: str, part: str, seq: int, data: bytes) -> dict[str, Any]:
    return ask({"op": "chunk", "id": broadcast, "part": part, "seq": seq, "size": len(data)}, data)


def feed(broadcast: str, fixture: str) -> list[object]:
    """One part: the pieces of a fixture in order. Says which of them the box took."""
    part = uuid.uuid4().hex
    taken = []
    for seq, file in enumerate(sorted((FIXTURES / fixture).glob("*.bin"))):
        taken.append(chunk(broadcast, part, seq, file.read_bytes()).get("ok"))
        time.sleep(SECONDS_PER_PIECE)
    return taken


def playlist(broadcast: str) -> list[str]:
    try:
        return (HLS / broadcast / "index.m3u8").read_text().splitlines()
    except OSError:
        return []


def listed(broadcast: str) -> list[str]:
    return [line for line in playlist(broadcast) if line and not line.startswith("#")]


def wait_for(condition: Callable[[], bool], seconds: float = LISTING_SECONDS) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def probe(segment: Path) -> dict[str, Any]:
    shown = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,duration,width,height",
            "-of",
            "json",
            str(segment),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    streams = json.loads(shown.stdout or "{}").get("streams", [])
    by_type = {stream["codec_type"]: stream for stream in streams}
    return {
        "name": segment.name,
        "video": by_type.get("video", {}).get("codec_name"),
        "audio": by_type.get("audio", {}).get("codec_name"),
        "seconds": float(by_type.get("video", {}).get("duration", 0)),
        "height": by_type.get("video", {}).get("height"),
    }


def main() -> None:
    fixture = sys.argv[1]
    broadcast = str(uuid.uuid4())
    seen: dict[str, Any] = {
        "id": broadcast,
        "start": ask({"op": "start", "id": broadcast, "stop": []}),
    }

    most_files = 0
    watching = threading.Event()

    def watch() -> None:
        nonlocal most_files
        while not watching.is_set():
            with contextlib.suppress(OSError):
                most_files = max(most_files, len(os.listdir(HLS / broadcast)))
            time.sleep(0.02)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    # One part. Its last segment is whole only once the part has ended, so the segments
    # that are looked at are the ones before it.
    seen["first_part_taken"] = feed(broadcast, fixture)
    wait_for(lambda: len(listed(broadcast)) >= 3)
    seen["segments_listed"] = len(listed(broadcast))
    seen["playable"] = ask({"op": "status"})["live"][0]["playable"]
    seen["playlist_of_one_part"] = playlist(broadcast)
    seen["segments"] = [
        probe(HLS / broadcast / name) for name in listed(broadcast)[:-1] or listed(broadcast)
    ]

    # A second part: the playlist marks the break and its numbers go on.
    names_before = listed(broadcast)
    seen["second_part_taken"] = feed(broadcast, fixture)
    seen["break_listed"] = wait_for(lambda: "#EXT-X-DISCONTINUITY" in playlist(broadcast))
    wait_for(lambda: len(listed(broadcast)) >= min(6, len(names_before) + 3))
    seen["playlist_of_two_parts"] = playlist(broadcast)

    # Bytes that are no video, the same in every run: refused, and the box goes on serving.
    noise = random.Random(0).randbytes(50_000)
    seen["random_bytes_as_a_part"] = chunk(broadcast, uuid.uuid4().hex, 0, noise)
    seen["status_after_random_bytes"] = ask({"op": "status"})

    watching.set()
    watcher.join()
    seen["most_files_at_once"] = most_files
    seen["files_before_stop"] = sorted(os.listdir(HLS / broadcast))
    seen["stop"] = ask({"op": "stop", "id": broadcast})
    seen["volume_after_stop"] = sorted(os.listdir(HLS))
    print(json.dumps(seen))


main()
