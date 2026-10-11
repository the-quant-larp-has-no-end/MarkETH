"""The ffmpeg command (SPEC §9), pinned word for word."""

from dataclasses import replace
from pathlib import Path

import pytest

from mediabox import ffmpeg
from mediabox.settings import Settings
from tests.unit.mediabox.conftest import ONE


def value_of(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_the_command_for_the_values_of_the_example_environment(settings: Settings) -> None:
    settings = replace(settings, hls_root=Path("/var/lib/marketh/hls"))
    assert ffmpeg.arguments(
        settings, ffmpeg.directory(settings, ONE), "matroska", continues=False
    ) == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-fflags",
        "+genpts+discardcorrupt",
        "-probesize",
        "500000",
        "-analyzeduration",
        "1000000",
        "-max_pixels",
        "2073600",
        "-protocol_whitelist",
        "pipe",
        "-codec_whitelist",
        "vp8,h264,opus,aac",
        "-f",
        "matroska",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=-2:'min(720,ih)',fps=24,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "main",
        "-threads",
        "2",
        "-b:v",
        "1200k",
        "-maxrate",
        "1200k",
        "-bufsize",
        "2400k",
        "-g",
        "48",
        "-keyint_min",
        "48",
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-af",
        "aresample=async=1000",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_list_size",
        "6",
        "-hls_delete_threshold",
        "2",
        "-hls_segment_type",
        "mpegts",
        "-hls_flags",
        "delete_segments+omit_endlist+temp_file+independent_segments+program_date_time",
        "-hls_segment_filename",
        f"/var/lib/marketh/hls/{ONE}/seg-%06d.ts",
        f"/var/lib/marketh/hls/{ONE}/index.m3u8",
    ]


def test_the_input_is_named_and_fenced_before_it_is_opened(settings: Settings) -> None:
    for container in ("matroska", "mov"):
        argv = ffmpeg.arguments(
            settings, ffmpeg.directory(settings, ONE), container, continues=False
        )
        opened = argv.index("-i")
        assert argv[opened + 1] == "pipe:0"
        # The three options of the review, each before `-i`: after it they would be the output's.
        assert argv.index("-f") < opened
        assert argv[argv.index("-f") + 1] == container
        assert argv.index("-codec_whitelist") < opened
        assert value_of(argv, "-codec_whitelist") == "vp8,h264,opus,aac"
        assert argv.index("-protocol_whitelist") < opened
        assert value_of(argv, "-protocol_whitelist") == "pipe"
        assert argv.index("-max_pixels") < opened


def test_a_later_process_continues_the_playlist_and_only_it(settings: Settings) -> None:
    first = ffmpeg.arguments(settings, ffmpeg.directory(settings, ONE), "matroska", continues=False)
    later = ffmpeg.arguments(settings, ffmpeg.directory(settings, ONE), "matroska", continues=True)
    assert "append_list" not in value_of(first, "-hls_flags")
    assert value_of(later, "-hls_flags") == value_of(first, "-hls_flags") + "+append_list"
    # `discont_start` would open every playlist with a break that is none.
    assert "discont_start" not in value_of(first, "-hls_flags") + value_of(later, "-hls_flags")
    assert [word for word in first if word != value_of(first, "-hls_flags")] == [
        word for word in later if word != value_of(later, "-hls_flags")
    ]


def test_the_products_and_the_settings_reach_the_command(settings: Settings) -> None:
    changed = replace(
        settings,
        video_fps=30,
        hls_segment_seconds=4,
        input_max_width=1280,
        input_max_height=720,
        video_height=480,
        video_kbps=800,
        audio_kbps=96,
        hls_playlist_segments=5,
        ffmpeg_threads=1,
        x264_preset="superfast",
    )
    argv = ffmpeg.arguments(changed, ffmpeg.directory(changed, ONE), "mov", continues=False)
    assert value_of(argv, "-g") == "120"
    assert value_of(argv, "-keyint_min") == "120"
    assert value_of(argv, "-max_pixels") == str(1280 * 720)
    assert value_of(argv, "-vf") == "scale=-2:'min(480,ih)',fps=30,format=yuv420p"
    assert (value_of(argv, "-b:v"), value_of(argv, "-maxrate"), value_of(argv, "-bufsize")) == (
        "800k",
        "800k",
        "1600k",
    )
    assert value_of(argv, "-b:a") == "96k"
    assert value_of(argv, "-hls_time") == "4"
    assert value_of(argv, "-hls_list_size") == "5"
    assert value_of(argv, "-threads") == "1"
    assert value_of(argv, "-preset") == "superfast"


def test_the_directory_is_the_id_s(settings: Settings) -> None:
    assert ffmpeg.directory(settings, ONE) == settings.hls_root / ONE
    argv = ffmpeg.arguments(settings, ffmpeg.directory(settings, ONE), "matroska", continues=False)
    assert argv[-1] == str(settings.hls_root / ONE / "index.m3u8")
    assert value_of(argv, "-hls_segment_filename") == str(settings.hls_root / ONE / "seg-%06d.ts")


@pytest.mark.parametrize(
    "broadcast_id",
    [
        "",
        "..",
        "../" + ONE,
        ONE + "/..",
        "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF",
        ONE[:-1],
        ONE + "0",
        "x" * 36,
        ONE.replace("-", "_"),
        ONE + "\n",
    ],
)
def test_an_id_that_is_no_uuid_is_no_path(settings: Settings, broadcast_id: str) -> None:
    with pytest.raises(ValueError, match="not a broadcast id"):
        ffmpeg.directory(settings, broadcast_id)
