"""`app.core.security`: hashes and the Crockford Base32 code format (SPEC §2)."""

import hashlib
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.security import (
    CROCKFORD_ALPHABET,
    format_code,
    generate_code,
    hash_code,
    normalize_code,
    sha256_hex,
)

codes = st.text(alphabet=CROCKFORD_ALPHABET, min_size=1, max_size=32)


def test_crockford_alphabet_has_32_symbols_and_no_lookalikes() -> None:
    assert len(CROCKFORD_ALPHABET) == len(set(CROCKFORD_ALPHABET)) == 32
    assert not set("ILOU") & set(CROCKFORD_ALPHABET)


def test_sha256_hex_is_the_standard_digest_in_lowercase_hex() -> None:
    assert sha256_hex("abc") == ("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    assert sha256_hex("Zürich") == hashlib.sha256("Zürich".encode()).hexdigest()
    assert len(sha256_hex("")) == 64


@pytest.mark.parametrize("length", [8, 12, 32])
def test_a_generated_code_has_the_asked_length_and_alphabet(length: int) -> None:
    code = generate_code(length)
    assert len(code) == length
    assert set(code) <= set(CROCKFORD_ALPHABET)


def test_generated_codes_do_not_repeat() -> None:
    assert len({generate_code(12) for _ in range(500)}) == 500


@pytest.mark.parametrize(
    ("typed", "normalized"),
    [
        ("ABCD-EFGH-JKMN", "ABCDEFGHJKMN"),
        ("abcd-efgh-jkmn", "ABCDEFGHJKMN"),
        ("  ABCD EFGH\tJKMN\n", "ABCDEFGHJKMN"),
        ("O0O0-0000-0000", "000000000000"),
        ("oooo", "0000"),
        ("I1L1", "1111"),
        ("il", "11"),
        ("LOIS-0123", "101S0123"),
        ("----A----", "A"),
    ],
)
def test_normalize_code_reads_what_a_person_typed(typed: str, normalized: str) -> None:
    """Uppercase, no dashes or whitespace, O read as 0, I and L read as 1 (SPEC §2)."""
    assert normalize_code(typed) == normalized


@pytest.mark.parametrize("typed", ["", "   ", "----", "ABCU", "ABC!", "AB_C", "ÄBCD", "AB.CD"])
def test_normalize_code_returns_none_for_what_cannot_be_a_code(typed: str) -> None:
    assert normalize_code(typed) is None


@pytest.mark.parametrize(
    ("code", "shown"),
    [
        ("ABCDEFGHJKMN", "ABCD-EFGH-JKMN"),
        ("ABCDEFGH", "ABCD-EFGH"),
        ("ABCDEFGHJK", "ABCD-EFGH-JK"),
        ("ABC", "ABC"),
        ("", ""),
    ],
)
def test_format_code_groups_by_four(code: str, shown: str) -> None:
    assert format_code(code) == shown


@given(codes)
def test_a_code_survives_being_formatted_and_typed_back(code: str) -> None:
    assert normalize_code(format_code(code)) == code
    assert normalize_code(format_code(code).lower()) == code


@given(codes)
def test_normalizing_is_idempotent(code: str) -> None:
    once = normalize_code(code)
    assert once is not None
    assert normalize_code(once) == once


def test_hash_code_is_the_sha256_of_the_normalized_code() -> None:
    normalized = normalize_code("abcd-efgh-jkmn")
    assert normalized is not None
    assert hash_code(normalized) == sha256_hex("ABCDEFGHJKMN")


def test_every_spelling_of_a_code_has_the_same_hash() -> None:
    spellings = ["O1AB-CDEF-GH23", "01ab cdef gh23", "oIAB-CDEF-GH23", "0LABCDEFGH23"]
    hashes = set()
    for spelling in spellings:
        normalized = normalize_code(spelling)
        assert normalized is not None
        hashes.add(hash_code(normalized))
    assert len(hashes) == 1


# --- A code is read the same way every time ----------------------------------------------

SEPARATORS = "- \t\n\r"
"""What a scanner or a keyboard puts between the characters of a code, and is dropped."""

WRITTEN_AS = {"0": "0Oo", "1": "1IiLl"}
"""Every way a zero and a one are written: O is read as 0, I and L as 1."""


@st.composite
def spellings(draw: st.DrawFn) -> tuple[str, str]:
    """A code, and one way of writing it: any case, a look-alike letter for a 0 or a 1,
    and separators anywhere, also in front and at the end."""
    code = draw(codes)
    written: list[str] = [draw(st.text(alphabet=SEPARATORS, max_size=2))]
    for character in code:
        as_typed = draw(st.sampled_from(WRITTEN_AS.get(character, character)))
        written.append(as_typed.lower() if draw(st.booleans()) else as_typed)
        written.append(draw(st.text(alphabet=SEPARATORS, max_size=2)))
    return code, "".join(written)


@given(spellings())
def test_however_a_code_is_written_it_is_read_as_that_code_and_hashes_like_it(
    spelling: tuple[str, str],
) -> None:
    """Case, dashes, spaces and line ends, O for 0 and I or L for 1: the scan of a
    voucher finds the row by this hash, so every way of writing a code finds the same
    row, the second time as the first."""
    code, written = spelling

    read = normalize_code(written)

    assert read == code
    assert normalize_code(written) == read
    assert hash_code(read) == hash_code(code)


@given(
    spelling=spellings(),
    foreign=st.characters(
        exclude_characters=CROCKFORD_ALPHABET + CROCKFORD_ALPHABET.lower() + SEPARATORS + "OoIiLl"
    ),
    data=st.data(),
)
def test_one_character_that_is_no_part_of_a_code_makes_the_whole_text_no_code(
    spelling: tuple[str, str], foreign: str, data: st.DataObject
) -> None:
    """A link, a slash, a colon, a letter U, a letter of another script, a dash that is
    not the hyphen: wherever it stands, the text is no code at all. Nothing is cut out
    of a text to find a code in it."""
    _, written = spelling
    place = data.draw(st.integers(min_value=0, max_value=len(written)))

    assert normalize_code(written[:place] + foreign + written[place:]) is None


@pytest.mark.parametrize(
    "scanned",
    [
        "https://09.hackathon.ethz.ch/wallet?code=ABCD-EFGH-JKMN",
        "http://localhost:8080/wallet?code=ABCD-EFGH",
        "/wallet?code=ABCD-EFGH",
        "code=ABCD-EFGH",
        "ABCD-EFGH?",
        "ABCD\u2013EFGH",
        "ABCD\u00a0EFGH",
        "\ufeffABCD-EFGH",
        "ABCD-EFGU",
        "ABCD-EFG\u0131",
        "ABCD-EFG\u017f",
        "ABCD-EF\u00df",
        "ABCD-EF\ufb01",
    ],
    ids=[
        "the-link-of-a-code-card",
        "a-link-with-a-voucher-code",
        "a-path",
        "a-query",
        "a-question-mark",
        "an-en-dash",
        "a-no-break-space",
        "a-byte-order-mark",
        "a-letter-u",
        "a-dotless-i",
        "a-long-s",
        "a-sharp-s",
        "a-ligature",
    ],
)
def test_a_link_and_a_code_with_a_foreign_character_are_no_code(scanned: str) -> None:
    """The last four are letters whose upper case is ASCII: I, S, SS and FI. A code is
    ASCII as it stands, so none of them is read as the character it turns into."""
    assert normalize_code(scanned) is None


def test_no_character_outside_ascii_is_read_as_a_character_of_a_code() -> None:
    """Every character there is: one that is not ASCII makes a text no code, also the
    ten whose upper case is made of code characters and look-alikes alone."""
    read_as_a_code = [
        chr(point)
        for point in range(128, sys.maxunicode + 1)
        if normalize_code(f"ABCD-EFG{chr(point)}") is not None
    ]
    turning_into_ascii = [
        chr(point)
        for point in range(128, sys.maxunicode + 1)
        if chr(point).upper().isascii() and chr(point).upper().isalnum()
    ]

    assert read_as_a_code == []
    assert {"\u00df", "\u0131", "\u017f", "\ufb01"} <= set(turning_into_ascii)
