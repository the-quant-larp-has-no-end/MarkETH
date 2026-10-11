"""The settings of the box (SPEC §9): each is required and held to its rule."""

import pytest

from mediabox.settings import HLS_ROOT, Settings, SettingsError
from tests.unit.mediabox.environment import BOX_VARIABLES, box_environment

# The variables Compose hands the box, with the values of `.example.env`.
ENVIRONMENT = box_environment()


def test_the_values_of_the_example_environment() -> None:
    assert sorted(ENVIRONMENT) == sorted(BOX_VARIABLES)
    settings = Settings.from_environment(ENVIRONMENT)
    assert str(settings.socket_path) == "/run/marketh-media/box.sock"
    assert settings.hls_root == HLS_ROOT
    assert (settings.max_live, settings.max_minutes, settings.stall_seconds) == (1, 120, 120)
    assert settings.queue_max_bytes == 4 * 1048576
    assert settings.input_max_pixels == 1920 * 1080
    assert settings.keyframe_interval == 48
    assert settings.x264_preset == "veryfast"


@pytest.mark.parametrize("name", sorted(ENVIRONMENT))
def test_every_variable_is_required(name: str) -> None:
    for environment in (
        {key: value for key, value in ENVIRONMENT.items() if key != name},
        {**ENVIRONMENT, name: ""},
        {**ENVIRONMENT, name: "   "},
    ):
        with pytest.raises(SettingsError, match=f"^{name}: is not set$"):
            Settings.from_environment(environment)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BROADCAST_MEDIA_SOCKET", "run/box.sock"),
        ("BROADCAST_MAX_LIVE", "0"),
        ("BROADCAST_MAX_LIVE", "1.5"),
        ("BROADCAST_MAX_LIVE", "-1"),
        ("BROADCAST_MAX_LIVE", "one"),
        ("BROADCAST_MAX_LIVE", "\uff11"),
        ("BROADCAST_MAX_MINUTES", "0"),
        ("BROADCAST_STALL_SECONDS", "9"),
        ("BROADCAST_CHUNK_MAX_BYTES", "1023"),
        ("BROADCAST_INPUT_MAX_WIDTH", "638"),
        ("BROADCAST_INPUT_MAX_WIDTH", "3842"),
        ("BROADCAST_INPUT_MAX_WIDTH", "1921"),
        ("BROADCAST_INPUT_MAX_HEIGHT", "358"),
        ("BROADCAST_INPUT_MAX_HEIGHT", "2162"),
        ("BROADCAST_INPUT_MAX_HEIGHT", "1081"),
        ("BROADCAST_VIDEO_HEIGHT", "238"),
        ("BROADCAST_VIDEO_HEIGHT", "1082"),
        ("BROADCAST_VIDEO_HEIGHT", "721"),
        ("BROADCAST_VIDEO_FPS", "9"),
        ("BROADCAST_VIDEO_FPS", "31"),
        ("BROADCAST_VIDEO_KBPS", "199"),
        ("BROADCAST_VIDEO_KBPS", "4001"),
        ("BROADCAST_AUDIO_KBPS", "31"),
        ("BROADCAST_AUDIO_KBPS", "193"),
        ("BROADCAST_HLS_SEGMENT_SECONDS", "0"),
        ("BROADCAST_HLS_SEGMENT_SECONDS", "7"),
        ("BROADCAST_HLS_PLAYLIST_SEGMENTS", "2"),
        ("BROADCAST_HLS_PLAYLIST_SEGMENTS", "21"),
        ("MEDIA_FFMPEG_THREADS", "0"),
        ("MEDIA_FFMPEG_THREADS", "9"),
        ("MEDIA_X264_PRESET", "medium"),
        ("MEDIA_X264_PRESET", "veryfast -vf movie=/etc/passwd"),
    ],
)
def test_a_breach_names_its_variable(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=f"^{name}: "):
        Settings.from_environment({**ENVIRONMENT, name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BROADCAST_STALL_SECONDS", "10"),
        ("BROADCAST_INPUT_MAX_WIDTH", "640"),
        ("BROADCAST_INPUT_MAX_WIDTH", "3840"),
        ("BROADCAST_VIDEO_HEIGHT", "240"),
        ("BROADCAST_VIDEO_HEIGHT", "1080"),
        ("BROADCAST_VIDEO_FPS", "10"),
        ("BROADCAST_VIDEO_FPS", "30"),
        ("BROADCAST_HLS_PLAYLIST_SEGMENTS", "20"),
        ("MEDIA_FFMPEG_THREADS", "8"),
        ("MEDIA_X264_PRESET", "ultrafast"),
        ("MEDIA_X264_PRESET", "fast"),
    ],
)
def test_the_edges_of_the_rules_are_inside(name: str, value: str) -> None:
    Settings.from_environment({**ENVIRONMENT, name: value})
