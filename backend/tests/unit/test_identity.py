"""Who is calling, as plain values (SPEC §3, Appendix A.2, A.3).

A registered caller has the rights of their role only while their account is active.
A caller the portal named who has no profile is a `Caller` without a user.

Then the two readings of the portal's headers: the display name a profile stores, and
the id. How strictly an id is read is `tests/unit/test_email_policy.py`.
"""

import re
from urllib.parse import quote
from uuid import uuid7

import pytest

from app.core.enums import UserRole, UserStatus
from app.core.identity import (
    DISPLAY_NAME_MAX_LENGTH,
    USERNAME_CHARACTERS,
    USERNAME_PATTERN,
    USERNAME_STORED_MAX_LENGTH,
    AuthenticatedUser,
    Caller,
    PortalIdentity,
    display_name,
    portal_address,
)


def someone(role: UserRole, status: UserStatus) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uuid7(),
        email="someone@ethz.ch",
        name="Some One",
        username="someone",
        degree_programme="BSc Computer Science",
        role=role,
        status=status,
    )


@pytest.mark.parametrize("role", list(UserRole))
def test_an_active_account_has_the_rights_of_its_role(role: UserRole) -> None:
    active = someone(role, UserStatus.ACTIVE)

    assert active.is_active
    assert active.is_admin == (role is UserRole.ADMIN)
    assert active.is_staff == (role in (UserRole.ADMIN, UserRole.BARISTA))


@pytest.mark.parametrize("role", list(UserRole))
def test_a_suspended_account_keeps_its_role_and_none_of_its_rights(role: UserRole) -> None:
    suspended = someone(role, UserStatus.SUSPENDED)

    assert suspended.role is role
    assert not suspended.is_active
    assert not suspended.is_admin
    assert not suspended.is_staff


def test_a_caller_without_a_user_is_an_identity_without_a_profile() -> None:
    identity = PortalIdentity(email="fresh@ethz.ch", name="", role=UserRole.ADMIN)

    caller = Caller(identity=identity, user=None)

    assert caller.user is None
    assert caller.identity.role is UserRole.ADMIN, "a bootstrap admin is one before a profile"
    assert caller.identity.name == "", "the portal may send no name"


def test_a_registered_caller_carries_the_identity_and_the_user() -> None:
    user = someone(UserRole.BARISTA, UserStatus.ACTIVE)
    identity = PortalIdentity(email=user.email, name="Some One", role=user.role)

    caller = Caller(identity=identity, user=user)

    assert caller.user is user
    assert (caller.identity.email, caller.identity.role) == (user.email, user.role)


# --- The display name ---------------------------------------------------------------

ZOE = "Zo" + chr(0xEB) + " M" + chr(0xFC) + "ller"

ZERO_WIDTH_SPACE = chr(0x200B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
SOFT_HYPHEN = chr(0xAD)
NO_BREAK_SPACE = chr(0xA0)
LINE_SEPARATOR = chr(0x2028)
IDEOGRAPHIC_SPACE = chr(0x3000)
NEXT_LINE = chr(0x85)
REPLACEMENT_CHARACTER = chr(0xFFFD)


def test_the_display_name_is_the_name_header_percent_decoded() -> None:
    """The portal percent-encodes what is outside ASCII, as UTF-8."""
    assert display_name("Zo%C3%AB%20M%C3%BCller") == ZOE
    assert display_name("Ada%20Admin") == "Ada Admin"
    assert display_name("Ada Admin") == "Ada Admin"
    assert display_name("O%27Neil%2C%20Shaquille") == "O'Neil, Shaquille"


@pytest.mark.parametrize(
    "raw", ["", "%20", "   ", "%00", "%00%00", "%0A", "%E2%80%8B", "%09%0D%0A"]
)
def test_a_header_that_holds_nothing_a_name_keeps_is_an_empty_name(raw: str) -> None:
    assert display_name(raw) == ""


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Ada%00Admin", "AdaAdmin"),
        ("%00Ada", "Ada"),
        ("Ada%00", "Ada"),
        ("Ada %00 Admin", "Ada Admin"),
        ("Ada%1BAdmin", "AdaAdmin"),
        ("Ada%7FAdmin", "AdaAdmin"),
        ("A%08da", "Ada"),
    ],
    ids=["nul-inside", "nul-first", "nul-last", "nul-between-spaces", "escape", "delete", "bs"],
)
def test_control_characters_are_removed(raw: str, name: str) -> None:
    """A NUL in particular: Postgres does not store one, so a name with it could not be
    saved at all."""
    assert display_name(raw) == name
    assert "\x00" not in display_name(raw)


@pytest.mark.parametrize(
    "invisible", [ZERO_WIDTH_SPACE, RIGHT_TO_LEFT_OVERRIDE, SOFT_HYPHEN, chr(0xFEFF), chr(0x200D)]
)
def test_format_characters_are_removed(invisible: str) -> None:
    """What reorders or hides the text around it has no place in a name others read."""
    assert display_name(f"Ada{invisible} Ad{invisible}min{invisible}") == "Ada Admin"
    assert display_name(quote(f"{invisible}Ada", safe="")) == "Ada"


@pytest.mark.parametrize(
    "raw",
    [
        "  Ada   Admin  ",
        "Ada\tAdmin",
        "Ada%09Admin",
        "Ada%0AAdmin",
        "Ada%0D%0AAdmin",
        "%09 Ada \t%0A%0D  Admin%0A",
        f"Ada{NO_BREAK_SPACE}Admin",
        f"Ada {NO_BREAK_SPACE}\t{NEXT_LINE} Admin",
        quote(f"Ada{LINE_SEPARATOR}Admin", safe=""),
        quote(f"Ada{IDEOGRAPHIC_SPACE}{IDEOGRAPHIC_SPACE}Admin", safe=""),
        "Ada%0B%0CAdmin",
    ],
    ids=[
        "spaces",
        "tab",
        "encoded-tab",
        "line-feed",
        "carriage-return-line-feed",
        "mixed",
        "no-break-space",
        "mixed-with-next-line",
        "line-separator",
        "ideographic-spaces",
        "vertical-tab-and-form-feed",
    ],
)
def test_every_run_of_whitespace_is_one_space_and_none_stays_at_the_ends(raw: str) -> None:
    """A tab or a line break is a control character too, but between two words it
    separates them: it becomes the space, it is not dropped."""
    assert display_name(raw) == "Ada Admin"


def test_a_name_of_300_characters_is_cut_to_200() -> None:
    assert DISPLAY_NAME_MAX_LENGTH == 200
    assert display_name("a" * 300) == "a" * 200
    assert display_name("a" * 200) == "a" * 200
    assert display_name("%C3%AB" * 300) == chr(0xEB) * 200, "characters are counted, not bytes"


def test_the_cut_leaves_no_space_at_the_end() -> None:
    assert display_name("a" * 199 + " b") == "a" * 199
    assert display_name("a" * 198 + " bcd") == "a" * 198 + " b"


def test_the_cut_counts_what_is_kept() -> None:
    """Removing and collapsing come first, so they cannot push a name over the width."""
    padded = "  " + ("ab" + ZERO_WIDTH_SPACE + "  ") * 150
    assert len(padded) > 2 * DISPLAY_NAME_MAX_LENGTH

    name = display_name(padded)

    assert name == ("ab " * 67)[:DISPLAY_NAME_MAX_LENGTH].rstrip()
    assert len(name) <= DISPLAY_NAME_MAX_LENGTH


def test_bytes_that_are_no_utf_8_do_not_fail_the_request() -> None:
    assert display_name("Ada%FF") == "Ada" + REPLACEMENT_CHARACTER
    assert display_name("100%") == "100%"
    assert display_name("%zz") == "%zz"


def test_a_name_is_decoded_once() -> None:
    """`%2500` is the text `%00`, not a NUL."""
    assert display_name("%2500") == "%00"
    assert display_name("Ada%2520Admin") == "Ada%20Admin"


@pytest.mark.parametrize(
    "raw",
    [
        "Zo%C3%AB%20M%C3%BCller",
        "  Ada %00\t Admin ",
        "a" * 300,
        "%E2%80%AEevil",
        "",
        "x" * 199 + " y",
    ],
)
def test_a_display_name_read_again_is_itself(raw: str) -> None:
    """What a profile stores needs no second cleaning (as long as it holds no `%`)."""
    name = display_name(raw)

    assert display_name(name) == name
    assert name == name.strip()
    assert "  " not in name
    assert len(name) <= DISPLAY_NAME_MAX_LENGTH


# --- The id --------------------------------------------------------------------------


def test_an_id_is_an_address_at_an_allowed_domain_in_lower_case() -> None:
    allowed = ["ethz.ch", "student.ethz.ch"]

    assert portal_address("dokwak@ethz.ch", allowed) == "dokwak@ethz.ch"
    assert portal_address("Dokwak@ETHZ.ch", allowed) == "dokwak@ethz.ch"
    assert portal_address("dokwak@student.ethz.ch", allowed) == "dokwak@student.ethz.ch"
    assert portal_address("dokwak@uzh.ch", allowed) is None
    assert portal_address("dokwak@student.ethz.ch", ["ethz.ch"]) is None
    assert portal_address("dokwak@ethz.ch", []) is None


@pytest.mark.parametrize("piece", ["dokwak@ethz", "dokwak@thz.ch", "dokwak@e", "dokwak@.", "x@ch"])
def test_a_domain_is_looked_up_among_whole_domains_never_inside_one(piece: str) -> None:
    """A piece of an allowed domain is not that domain."""
    assert portal_address(piece, ["ethz.ch"]) is None
    assert portal_address(piece, ("ethz.ch",)) is None
    assert portal_address(piece, frozenset({"ethz.ch"})) is None


def test_one_domain_as_a_bare_string_is_a_mistake_not_a_lookup() -> None:
    """Looking `ethz` up in the text `ethz.ch` would find it."""
    for raw in ("dokwak@ethz.ch", "dokwak@ethz", "nobody"):
        with pytest.raises(TypeError, match="a collection of domains"):
            portal_address(raw, "ethz.ch")


def test_the_username_pattern_is_lower_case_and_bounds_the_length() -> None:
    assert USERNAME_PATTERN == "^[a-z0-9_]{1,32}$"
    assert (USERNAME_CHARACTERS, USERNAME_STORED_MAX_LENGTH) == ("a-z0-9_", 32)
    username = re.compile(USERNAME_PATTERN)
    for name in ("neo", "n", "max_muster_99", "_", "007", "x" * 32):
        assert username.fullmatch(name), name
    for name in ("Neo", "NEO", "", "x" * 33, "bob smith", "bob-smith", "zo" + chr(0xEB), "bob\n"):
        assert username.fullmatch(name) is None, name
