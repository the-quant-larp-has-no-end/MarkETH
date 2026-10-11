"""What a part is (its first bytes), and what a playlist lists."""

from pathlib import Path

import pytest

from mediabox import playlist
from mediabox.sniff import container_of


def box(kind: bytes, size: int = 24) -> bytes:
    return size.to_bytes(4) + kind + b"isom" + b"\0" * 64


@pytest.mark.parametrize(
    ("first", "container"),
    [
        (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01", "matroska"),
        (box(b"ftyp"), "mov"),
        (box(b"ftyp", 36), "mov"),
        (box(b"styp"), "mov"),
        (box(b"moof", 2884), "mov"),
        (box(b"moov"), None),
        (box(b"mdat"), None),
        (box(b"free"), None),
        (b"ftyp" + b"\0" * 64, None),
        (b"", None),
        (b"\x1a\x45\xdf", None),
        (b"ftyp", None),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 64, None),
        (b"#EXTM3U\n#EXT-X-VERSION:3\n", None),
        (b"RIFF\x00\x00\x00\x00AVI LIST", None),
        (b"\x00\x00\x01\xba" + b"\0" * 64, None),
        (b"GIF89a" + b"\0" * 64, None),
        (b"ffconcat version 1.0\nfile /etc/passwd\n", None),
        (b"<?xml version='1.0'?><MPD>", None),
        (b"OggS" + b"\0" * 64, None),
    ],
)
def test_a_part_is_webm_or_mp4_or_it_is_not_taken(first: bytes, container: str | None) -> None:
    assert container_of(first) == container


def write_playlist(directory: Path, names: list[str]) -> None:
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:4"]
    for name in names:
        lines += [
            "#EXTINF:2.000000,",
            "#EXT-X-PROGRAM-DATE-TIME:2026-10-10T15:51:56.549+0000",
            name,
        ]
    (directory / "index.m3u8").write_text("\n".join(lines) + "\n")


def test_what_a_playlist_lists(tmp_path: Path) -> None:
    assert playlist.listed(tmp_path) == []
    write_playlist(tmp_path, ["seg-000004.ts", "seg-000005.ts"])
    assert playlist.listed(tmp_path) == ["seg-000004.ts", "seg-000005.ts"]
    (tmp_path / "index.m3u8").write_bytes(b"\xff\xfe not a playlist \x00")
    assert playlist.listed(tmp_path) == ["�� not a playlist \x00"]


def test_sweeping_deletes_what_the_playlist_does_not_list(tmp_path: Path) -> None:
    write_playlist(tmp_path, ["seg-000004.ts", "seg-000005.ts"])
    for name in (
        "seg-000002.ts",
        "seg-000003.ts",
        "seg-000004.ts",
        "seg-000005.ts",
        "seg-000006.ts.tmp",
        "index.m3u8.tmp",
    ):
        (tmp_path / name).write_bytes(b"x")
    assert playlist.sweep(tmp_path) == 4
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "index.m3u8",
        "seg-000004.ts",
        "seg-000005.ts",
    ]
    assert playlist.sweep(tmp_path) == 0


def test_sweeping_a_directory_without_a_playlist_empties_it(tmp_path: Path) -> None:
    (tmp_path / "seg-000000.ts.tmp").write_bytes(b"x")
    assert playlist.sweep(tmp_path) == 1
    assert list(tmp_path.iterdir()) == []
    assert playlist.sweep(tmp_path / "nowhere") == 0
