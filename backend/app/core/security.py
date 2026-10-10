"""Secrets: generation, hashing and the Crockford Base32 code format (SPEC §2).

A code and the code of a voucher are looked up by the SHA-256 hash of the value the
user holds. The code of a voucher is stored as that hash alone. A code that gives
credits is also kept as it is while it can still be redeemed, so that an admin can
print its card again (`app.models.code`).
"""

import hashlib
import secrets
from typing import Final

CROCKFORD_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Crockford Base32: no I, L, O or U, so codes survive being read aloud or handwritten."""

_CROCKFORD_SET: Final[frozenset[str]] = frozenset(CROCKFORD_ALPHABET)
_LOOKALIKES: Final[dict[int, str]] = str.maketrans({"O": "0", "I": "1", "L": "1"})
_GROUP_SIZE: Final[int] = 4


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_code(length: int) -> str:
    """A random Crockford Base32 code (5 bits per character), without separators."""
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(length))


def normalize_code(raw: str) -> str | None:
    """Canonical form of a code as typed or scanned, or None if it cannot be a code.

    Uppercases, drops dashes and whitespace, and reads O as 0 and I or L as 1.
    """
    cleaned = "".join(ch for ch in raw.upper() if ch not in "- \t\n\r").translate(_LOOKALIKES)
    if not cleaned or any(ch not in _CROCKFORD_SET for ch in cleaned):
        return None
    return cleaned


def format_code(code: str) -> str:
    """Display form: groups of four separated by dashes, e.g. `XXXX-XXXX-XXXX`."""
    return "-".join(code[i : i + _GROUP_SIZE] for i in range(0, len(code), _GROUP_SIZE))


def hash_code(normalized_code: str) -> str:
    return sha256_hex(normalized_code)
