"""Which container a part is, read from its first bytes (SPEC §9).

ffmpeg is never left to guess: Alpine's build knows some hundreds of formats, and three
browsers send two. A part that begins as anything else is refused before an ffmpeg is
started for it, and the two that are taken are handed to ffmpeg by name.
"""

from typing import Final, Literal

# The names ffmpeg's `-f` takes for the two demuxers.
type Container = Literal["matroska", "mov"]

# A WebM file is a Matroska file: it begins with the id of the EBML header.
_EBML: Final = b"\x1a\x45\xdf\xa3"
# An MP4 file begins with a box: four bytes of length, then its name. A recorder's first
# box is `ftyp`, and the `moov` that names the codecs follows it. A part that begins with
# a fragment (`styp`, `moof`) is MP4 as well and is handed to the MP4 reader, which ends
# at once for want of a `moov`: the part is dead, and the next request begins another.
_MP4_BOXES: Final = (b"ftyp", b"styp", b"moof")


def container_of(first: bytes) -> Container | None:
    """The container of a part whose first bytes these are, or None for anything else."""
    if first[:4] == _EBML:
        return "matroska"
    if first[4:8] in _MP4_BOXES:
        return "mov"
    return None
