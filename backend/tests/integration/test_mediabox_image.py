"""The image of the media box, built and run as compose.yaml runs it (SPEC §9).

Marked `media`: it builds the image and needs Docker, so it is deselected by default like
the ScyllaDB tests (`uv run pytest -m media`). The box runs without a network; what talks
to it runs beside it in a second container, as the API does, because a Unix socket on a
Docker volume cannot be reached from a host that is not Linux.

What is fed are pieces of real recorders (tests/fixtures/broadcast/README.txt). A fixture
that nobody has recorded is skipped: what its browser sends is then not proved.
"""

import json
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest

import mediabox
from mediabox.broadcast import PLAYABLE_SEGMENTS
from mediabox.ffmpeg import KEPT_AFTER_PLAYLIST, PLAYLIST_NAME
from mediabox.settings import HLS_ROOT
from tests.unit.mediabox.environment import box_environment, example_environment

pytestmark = pytest.mark.media

PACKAGE: Final = Path(mediabox.__file__).parent
FIXTURES: Final = Path(__file__).resolve().parents[1] / "fixtures" / "broadcast"
BESIDE: Final = "beside_the_box.py"

# The variables Compose hands the box, and what `.example.env` says about its container.
ENVIRONMENT: Final = box_environment()
EXAMPLE: Final = example_environment()
SOCKET: Final = Path(ENVIRONMENT["BROADCAST_MEDIA_SOCKET"])
SEGMENT_SECONDS: Final = int(ENVIRONMENT["BROADCAST_HLS_SEGMENT_SECONDS"])
PLAYLIST_SEGMENTS: Final = int(ENVIRONMENT["BROADCAST_HLS_PLAYLIST_SEGMENTS"])
VIDEO_HEIGHT: Final = int(ENVIRONMENT["BROADCAST_VIDEO_HEIGHT"])
VIDEO_FPS: Final = int(ENVIRONMENT["BROADCAST_VIDEO_FPS"])

# What the test names its image, its containers and its volumes with: nothing of a stack.
PREFIX: Final = "mediabox-test"

# The users of the two images: the box's (mediabox/Dockerfile) and the API's (Dockerfile).
BOX_USER: Final = 10001
API_USER: Final = 1000

# A request sent from inside the box: the address of the socket, then the request.
FROM_INSIDE: Final = """
import socket, sys
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(5)
    connection.connect(sys.argv[1])
    connection.sendall(sys.argv[2].encode() + b"\\n")
    print(connection.makefile("rb").readline().decode())
"""


def pieces_of(fixture: str) -> list[Path]:
    return sorted((FIXTURES / fixture).glob("*.bin"))


def manifest_of(fixture: str) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((FIXTURES / fixture / "manifest.json").read_text())
    return manifest


def recorded(fixture: str) -> Any:
    """A fixture as a case of the test, skipped while its directory holds no piece."""
    reason = f"no fixture {fixture}: frontend/e2e/record-fixtures.mjs records it"
    return pytest.param(fixture, marks=pytest.mark.skipif(not pieces_of(fixture), reason=reason))


def docker(*arguments: str, check: bool = True) -> str:
    done = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f"docker {' '.join(arguments[:3])}...: {done.stderr.strip()[-600:]}")
    return done.stdout.strip()


@pytest.fixture(scope="module")
def image() -> Iterator[str]:
    """The image under test, built from the package as compose.yaml builds it."""
    name = f"{PREFIX}:{uuid.uuid4().hex[:8]}"
    docker("build", "--quiet", "--tag", name, str(PACKAGE))
    yield name
    docker("rmi", name, check=False)


@dataclass(frozen=True)
class RunningBox:
    name: str
    image: str
    socket_volume: str
    hls_volume: str

    def beside(self, fixture: str) -> dict[str, Any]:
        """Runs tests/fixtures/broadcast/beside_the_box.py where the API and the proxy stand."""
        out = docker(
            "run", "--rm", "--name", f"{self.name}-beside",
            "--network", "none", "--user", f"{API_USER}:{API_USER}", "--read-only",
            "--cap-drop", "ALL", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--volume", f"{self.socket_volume}:{SOCKET.parent}:ro",
            "--volume", f"{self.hls_volume}:{HLS_ROOT}:ro",
            "--volume", f"{FIXTURES}:/fixtures:ro",
            self.image, "python", f"/fixtures/{BESIDE}", fixture, str(SOCKET), str(HLS_ROOT),
        )  # fmt: skip
        seen: dict[str, Any] = json.loads(out.splitlines()[-1])
        return seen

    def inside(self, request: dict[str, Any]) -> dict[str, Any]:
        """One request to the socket from a process of the box itself, as its own user."""
        out = docker(
            "exec", self.name, "python", "-c", FROM_INSIDE, str(SOCKET), json.dumps(request)
        )
        answer: dict[str, Any] = json.loads(out)
        return answer

    def state(self) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(
            docker("inspect", "--format", "{{json .State}}", self.name)
        )
        return state


def tmpfs_volume(name: str, options: str) -> None:
    docker(
        "volume", "create", "--driver", "local",
        "--opt", "type=tmpfs", "--opt", "device=tmpfs", "--opt", f"o={options}", name,
    )  # fmt: skip


@pytest.fixture
def box(image: str) -> Iterator[RunningBox]:
    """The box as the service `media` of compose.yaml, without its restart policy."""
    mark = uuid.uuid4().hex[:8]
    running = RunningBox(
        f"{PREFIX}-{mark}", image, f"{PREFIX}-socket-{mark}", f"{PREFIX}-hls-{mark}"
    )
    variables = [
        word for name, value in ENVIRONMENT.items() for word in ("--env", f"{name}={value}")
    ]
    try:
        tmpfs_volume(running.socket_volume, f"size=64k,mode=0750,uid={BOX_USER},gid={API_USER}")
        tmpfs_volume(
            running.hls_volume,
            f"size={EXAMPLE['MEDIA_HLS_TMPFS_SIZE']},mode=0755,uid={BOX_USER},gid={BOX_USER}",
        )
        docker(
            "run", "--detach", "--name", running.name,
            "--network", "none", "--read-only", "--ipc", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", EXAMPLE["MEDIA_PIDS_LIMIT"],
            "--memory", EXAMPLE["MEDIA_MEMORY_LIMIT"],
            "--cpus", EXAMPLE["MEDIA_CPUS"],
            *variables,
            "--volume", f"{running.socket_volume}:{SOCKET.parent}",
            "--volume", f"{running.hls_volume}:{HLS_ROOT}",
            image,
        )  # fmt: skip
        probe = ["docker", "exec", running.name, "python", "-m", "mediabox.probe"]
        for _ in range(100):
            if subprocess.run(probe, capture_output=True, check=False).returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"the box never answered: {docker('logs', running.name)[-600:]}")
        yield running
    finally:
        docker("rm", "--force", running.name, check=False)
        docker("volume", "rm", running.socket_volume, running.hls_volume, check=False)


def names_of(playlist: list[str]) -> list[str]:
    return [line for line in playlist if line and not line.startswith("#")]


@pytest.mark.parametrize(
    "fixture",
    [
        recorded("chrome-webm"),
        recorded("chrome-mp4"),
        recorded("firefox-webm"),
        recorded("webkit-mp4"),
        recorded("safari-mp4"),
    ],
)
def test_a_browser_s_pieces_become_hls_and_the_box_ends_with_its_broadcast(
    box: RunningBox, fixture: str
) -> None:
    manifest = manifest_of(fixture)
    assert (
        json.loads(docker("inspect", "--format", "{{json .HostConfig.NetworkMode}}", box.name))
        == "none"
    )
    seen = box.beside(fixture)

    # The pieces went in one request at a time and came out as a playlist that can be played.
    assert seen["start"] == {"ok": True}
    assert seen["first_part_taken"] == [True] * len(pieces_of(fixture))
    assert seen["segments_listed"] >= PLAYABLE_SEGMENTS
    assert seen["playable"] is True

    one_part = seen["playlist_of_one_part"]
    assert one_part[0] == "#EXTM3U"
    assert f"#EXT-X-TARGETDURATION:{SEGMENT_SECONDS}" in one_part
    assert "#EXT-X-DISCONTINUITY" not in one_part, "the first part opens with no break"
    assert "#EXT-X-ENDLIST" not in one_part
    assert names_of(one_part)[0] == "seg-000000.ts"

    # H.264 and AAC, whole segments within a frame, never scaled up.
    assert len(seen["segments"]) >= 2
    for segment in seen["segments"]:
        sound = "aac" if manifest["sound"] else None
        assert (segment["video"], segment["audio"]) == ("h264", sound), segment
        assert abs(segment["seconds"] - SEGMENT_SECONDS) <= 1 / VIDEO_FPS + 0.001, segment
        assert segment["height"] == min(VIDEO_HEIGHT, manifest["height"]), segment

    # A second part: one break is marked, and the numbers go on without a gap.
    assert seen["second_part_taken"] == [True] * len(pieces_of(fixture))
    assert seen["break_listed"]
    two_parts = seen["playlist_of_two_parts"]
    assert two_parts.count("#EXT-X-DISCONTINUITY") == 1
    numbers = [int(name[4:10]) for name in names_of(two_parts)]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    first_of_second = names_of(two_parts)[
        sum(
            1
            for line in two_parts[: two_parts.index("#EXT-X-DISCONTINUITY")]
            if line.endswith(".ts")
        )
    ]
    assert int(first_of_second[4:10]) > 0

    # Bytes that are no video are refused before an ffmpeg is started for them.
    assert seen["random_bytes_as_a_part"] == {"ok": False, "error": "restart"}
    assert seen["status_after_random_bytes"]["ok"] is True
    assert seen["status_after_random_bytes"]["live"][0]["encoder"] == "running"

    # Never more files than the playlist, the ones kept for late players, and one in the making.
    assert seen["most_files_at_once"] <= 1 + PLAYLIST_SEGMENTS + KEPT_AFTER_PLAYLIST + 1
    assert PLAYLIST_NAME in seen["files_before_stop"]

    # The stop leaves the volume empty, and the process ends with 0: an empty box follows.
    assert seen["stop"] == {"ok": True}
    assert seen["volume_after_stop"] == []
    docker("wait", box.name)
    state = box.state()
    assert (state["Status"], state["ExitCode"], state["OOMKilled"]) == ("exited", 0, False)
    log = docker("logs", box.name)
    assert log.count('"event":"encoder_started"') == 2, "one ffmpeg per part, none for the rest"
    assert '"event":"part_refused"' in log
    assert '"event":"exit"' in log


def test_a_process_inside_the_box_is_told_the_status_and_nothing_else(box: RunningBox) -> None:
    # ffmpeg runs beside the socket as the box's own user, and so would whatever a hostile
    # upload made of it. Such a peer cannot take the one place or stop a broadcast.
    broadcast = str(uuid.uuid4())
    refused = {"ok": False, "error": "bad_request"}
    assert box.inside({"op": "start", "id": broadcast, "stop": []}) == refused
    assert box.inside({"op": "stop", "id": broadcast}) == refused
    assert box.inside({"op": "status"}) == {
        "ok": True,
        "capacity": int(ENVIRONMENT["BROADCAST_MAX_LIVE"]),
        "live": [],
    }
    assert json.loads(docker("exec", box.name, "id", "-u")) == BOX_USER
