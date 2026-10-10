"""`app.core.security`: hashes and the Crockford Base32 code format (SPEC §2)."""

import hashlib

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
