"""`EmailPolicy`: whom the portal's id stands for, and who is an admin from the start
(SPEC §3, A.3).

The id is an identifier another system asserts, so it is read strictly: what is not
exactly an allowed address is refused, never read as the nearest known one. An accepted
id is the account, in lower case and at the domain it came with.

A role is kept with the account. The addresses of `ADMIN_EMAILS` are the exception: each
is an admin whatever its account says.
"""

import pytest

from app.core.config import Settings
from app.core.enums import UserRole
from app.core.errors import ForbiddenError
from app.services.email_policy import EmailPolicy
from tests.support.settings import EXAMPLE_ENV_FILE

ADMIN = "admin@ethz.ch"
STUDENT = "student@ethz.ch"

# Characters that look like, or fold onto, the ASCII ones of dokwak@ethz.ch. A request
# header is decoded as Latin-1, so the first two can arrive in one for certain.
MASCULINE_ORDINAL = chr(0xBA)  # NFKC makes it "o"
FEMININE_ORDINAL = chr(0xAA)  # NFKC makes it "a"
KELVIN_SIGN = chr(0x212A)  # lowercases to "k"
FULLWIDTH_H = chr(0xFF48)
FULLWIDTH_AT = chr(0xFF20)
CYRILLIC_IE = chr(0x0435)
O_UMLAUT = chr(0xF6)
LOOKALIKE_OF_DOKWAK = f"d{MASCULINE_ORDINAL}kw{FEMININE_ORDINAL}k@ethz.ch"


def policy_with(base_settings: Settings, **identity: object) -> EmailPolicy:
    """A policy on the test settings with some values of its identity section replaced."""
    section = base_settings.identity.model_copy(update=identity)
    return EmailPolicy(base_settings.model_copy(update={"identity": section}))


@pytest.fixture
def policy(base_settings: Settings) -> EmailPolicy:
    """The policy of `.example.env`: one allowed domain, one bootstrap admin."""
    return policy_with(base_settings, admin_emails=[ADMIN])


@pytest.fixture
def two_domains(base_settings: Settings) -> EmailPolicy:
    """A policy that is open to the addresses of two domains."""
    return policy_with(
        base_settings,
        allowed_email_domains=["ethz.ch", "student.ethz.ch"],
        admin_emails=[ADMIN],
    )


# --- identify -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["dokwak@ethz.ch", "first.last@ethz.ch", "a@ethz.ch", "x_y-z.9@ethz.ch", "9lives@ethz.ch"],
)
def test_an_address_at_the_allowed_domain_is_its_own_identity(
    policy: EmailPolicy, raw: str
) -> None:
    assert policy.identify(raw) == raw


def test_the_example_env_is_open_to_one_domain(
    policy: EmailPolicy, base_settings: Settings
) -> None:
    """With one allowed domain no two ids of the portal can be one person's."""
    assert base_settings.identity.allowed_email_domains == ["ethz.ch"]

    assert policy.identify("dokwak@ethz.ch") == "dokwak@ethz.ch"
    with pytest.raises(ForbiddenError):
        policy.identify("dokwak@student.ethz.ch")


@pytest.mark.parametrize(
    "raw",
    ["dokwak@uzh.ch", "dokwak@student.ethz.ch", "dokwak@epfl.ch", "dokwak@ethz.de", "dokwak@ch"],
)
def test_an_id_of_a_domain_that_is_not_allowed_is_403(policy: EmailPolicy, raw: str) -> None:
    with pytest.raises(ForbiddenError) as refused:
        policy.identify(raw)

    assert refused.value.status_code == 403
    assert refused.value.code.value == "FORBIDDEN"


def test_with_two_allowed_domains_one_local_part_is_two_different_ids(
    two_domains: EmailPolicy,
) -> None:
    """No domain is rewritten onto another: the id is the account, and the same name at
    another domain is another account with a wallet of its own."""
    at_eth = two_domains.identify("dokwak@ethz.ch")
    at_student = two_domains.identify("dokwak@student.ethz.ch")

    assert (at_eth, at_student) == ("dokwak@ethz.ch", "dokwak@student.ethz.ch")
    assert at_eth != at_student
    assert two_domains.identify("DOKWAK@Student.ETHZ.ch") == at_student


def test_a_bootstrap_admin_at_one_domain_is_none_at_the_other(two_domains: EmailPolicy) -> None:
    """`admin@ethz.ch` is in ADMIN_EMAILS; whoever the portal names
    `admin@student.ethz.ch` is somebody else."""
    assert two_domains.is_bootstrap_admin(two_domains.identify("admin@ethz.ch"))
    assert not two_domains.is_bootstrap_admin(two_domains.identify("admin@student.ethz.ch"))


@pytest.mark.parametrize(
    "raw", ["DOKWAK@ETHZ.CH", "Dokwak@Ethz.ch", "dokwak@ETHZ.ch", "dOkWaK@eThZ.cH"]
)
def test_upper_case_input_maps_to_the_lower_case_id(policy: EmailPolicy, raw: str) -> None:
    assert policy.identify(raw) == "dokwak@ethz.ch"


@pytest.mark.parametrize(
    "raw",
    [
        " dokwak@ethz.ch",
        "dokwak@ethz.ch ",
        "dokwak@ethz.ch\n",
        "\tdokwak@ethz.ch",
        "\xa0dokwak@ethz.ch",
        "dokwak@ethz.ch\x85",
        "dokwak@ethz.ch\x00",
    ],
    ids=["leading-space", "trailing-space", "newline", "tab", "no-break-space", "nel", "nul"],
)
def test_padding_is_not_trimmed_away(policy: EmailPolicy, raw: str) -> None:
    """An id with something around it is not the id without it."""
    with pytest.raises(ForbiddenError):
        policy.identify(raw)


@pytest.mark.parametrize(
    "raw",
    [
        LOOKALIKE_OF_DOKWAK,
        f"dokwa{KELVIN_SIGN}@ethz.ch",
        f"dokwak@ethz.c{FULLWIDTH_H}",
        f"d{O_UMLAUT}kwak@ethz.ch",
        f"dokwak{FULLWIDTH_AT}ethz.ch",
        f"dokwak@{CYRILLIC_IE}thz.ch",
    ],
    ids=[
        "ordinal-indicators",
        "kelvin-sign",
        "fullwidth-letter",
        "umlaut",
        "fullwidth-at",
        "cyrillic-e",
    ],
)
def test_no_character_outside_ascii_is_folded_onto_a_letter(policy: EmailPolicy, raw: str) -> None:
    """NFKC would turn the ordinal indicators into "o" and "a", and lowercasing turns
    the Kelvin sign into "k": each of these would become dokwak@ethz.ch."""
    with pytest.raises(ForbiddenError):
        policy.identify(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "@",
        "dokwak",
        "@ethz.ch",
        "dokwak@",
        "dokwak@@ethz.ch",
        "dokwak@ethz.ch@ethz.ch",
        "a@ethz.ch, b@ethz.ch",
        "a@ethz.ch,b@ethz.ch",
        "dokwak+admin@ethz.ch",
        '"dokwak"@ethz.ch',
        "do kwak@ethz.ch",
        ".dokwak@ethz.ch",
        "-dokwak@ethz.ch",
        "dokwak@ethz.ch.",
        f"{'a' * 65}@ethz.ch",
        "dokwak@uzh.ch",
        "dokwak@mail.ethz.ch",
        "dokwak@ethz.ch.example.org",
        "dokwak@notethz.ch",
        "dokwak%40ethz.ch",
    ],
)
def test_what_is_not_exactly_an_allowed_address_is_refused(policy: EmailPolicy, raw: str) -> None:
    with pytest.raises(ForbiddenError):
        policy.identify(raw)


def test_the_longest_local_part_has_64_characters(policy: EmailPolicy) -> None:
    longest = f"{'a' * 64}@ethz.ch"

    assert policy.identify(longest) == longest


def test_a_refusal_is_403_and_names_the_allowed_domains(policy: EmailPolicy) -> None:
    with pytest.raises(ForbiddenError) as refused:
        policy.identify("someone@uzh.ch")

    error = refused.value
    assert (error.status_code, error.code.value) == (403, "FORBIDDEN")
    assert error.details == {"allowed_domains": ["ethz.ch"]}
    assert error.message == "MarkETH is only open to accounts at ethz.ch."
    assert "someone" not in error.message, "the id that was refused is not echoed"


def test_a_refusal_names_every_allowed_domain(two_domains: EmailPolicy) -> None:
    with pytest.raises(ForbiddenError) as refused:
        two_domains.identify("someone@uzh.ch")

    assert refused.value.details == {"allowed_domains": ["ethz.ch", "student.ethz.ch"]}
    assert refused.value.message == (
        "MarkETH is only open to accounts at ethz.ch or student.ethz.ch."
    )


def test_a_malformed_id_and_a_foreign_domain_get_the_same_answer(policy: EmailPolicy) -> None:
    answers = []
    for raw in ("not an address", "someone@uzh.ch"):
        with pytest.raises(ForbiddenError) as refused:
            policy.identify(raw)
        answers.append((refused.value.message, refused.value.details))

    assert answers[0] == answers[1]


# --- Bootstrap admins and the role in effect -----------------------------------------


def test_an_address_of_admin_emails_is_a_bootstrap_admin_and_nobody_else_is(
    policy: EmailPolicy,
) -> None:
    assert policy.is_bootstrap_admin(ADMIN)
    assert not policy.is_bootstrap_admin(STUDENT)
    assert policy.bootstrap_admins == frozenset({ADMIN})


@pytest.mark.parametrize("stored", list(UserRole))
def test_a_bootstrap_admin_is_an_admin_whatever_its_row_says(
    policy: EmailPolicy, stored: UserRole
) -> None:
    """Also before it has a row: USER is what is passed for somebody without one."""
    assert policy.effective_role(ADMIN, stored) is UserRole.ADMIN


@pytest.mark.parametrize("stored", list(UserRole))
def test_everybody_else_has_the_role_of_their_row(policy: EmailPolicy, stored: UserRole) -> None:
    """An admin the panel made is one by the row alone, and so is a barista."""
    assert policy.effective_role(STUDENT, stored) is stored


def test_every_address_of_the_configured_list_is_a_bootstrap_admin(base_settings: Settings) -> None:
    """The list of `.example.env`, and the one the tests run with in its place, are
    written so that the policy can read them."""
    for settings in (Settings.load(EXAMPLE_ENV_FILE), base_settings):
        identity = settings.identity
        policy = EmailPolicy(settings)

        assert identity.admin_emails, "an admin is named"
        assert policy.bootstrap_admins == frozenset(identity.admin_emails)
        for address in identity.admin_emails:
            assert policy.is_bootstrap_admin(policy.identify(address))
            assert policy.effective_role(policy.identify(address), UserRole.USER) is UserRole.ADMIN
        nobody_special = policy.identify("nobody.special@ethz.ch")
        assert not policy.is_bootstrap_admin(nobody_special)
        assert policy.effective_role(nobody_special, UserRole.USER) is UserRole.USER


def test_a_list_entry_is_read_the_way_an_id_is(base_settings: Settings) -> None:
    """An entry written with capitals names the id the portal sends in any case."""
    written = policy_with(base_settings, admin_emails=["Ada@ETHZ.ch"])

    assert written.bootstrap_admins == frozenset({"ada@ethz.ch"})
    assert written.is_bootstrap_admin(written.identify("ada@ethz.ch"))
    assert written.is_bootstrap_admin(written.identify("ADA@ethz.ch"))
    assert not written.is_bootstrap_admin(written.identify("carol@ethz.ch"))


def test_an_admin_is_one_whichever_case_the_portal_sends_the_id_in(policy: EmailPolicy) -> None:
    for raw in ("admin@ethz.ch", "ADMIN@ethz.ch", "Admin@ETHZ.CH"):
        assert policy.effective_role(policy.identify(raw), UserRole.USER) is UserRole.ADMIN


@pytest.mark.parametrize(
    "entry",
    [
        "admin@uzh.ch",
        "admin@student.ethz.ch",
        "not-an-address",
        " padded@ethz.ch",
        "padded@ethz.ch ",
        LOOKALIKE_OF_DOKWAK,
        "a@ethz.ch, b@ethz.ch",
        "admin+vis@ethz.ch",
        "",
    ],
    ids=[
        "foreign-domain",
        "domain-that-is-not-allowed",
        "no-address",
        "leading-space",
        "trailing-space",
        "lookalike",
        "two-addresses",
        "plus-tag",
        "empty",
    ],
)
def test_an_entry_the_rule_refuses_stops_the_policy_from_being_built(
    base_settings: Settings, entry: str
) -> None:
    """Such an entry would make nobody an admin. The process does not start on it:
    `Container.build` constructs the policy."""
    with pytest.raises(ValueError, match="ADMIN_EMAILS") as refused:
        policy_with(base_settings, admin_emails=[ADMIN, entry])

    assert repr(entry) in str(refused.value)


def test_an_empty_list_makes_nobody_an_admin(base_settings: Settings) -> None:
    nobody = policy_with(base_settings, admin_emails=[])

    assert nobody.bootstrap_admins == frozenset()
    assert not nobody.is_bootstrap_admin(ADMIN)
    assert nobody.effective_role(ADMIN, UserRole.USER) is UserRole.USER
    assert nobody.effective_role(ADMIN, UserRole.ADMIN) is UserRole.ADMIN, "the row still counts"


def test_an_address_taken_off_the_list_has_the_role_of_its_row(base_settings: Settings) -> None:
    """Nothing remembers that it was a bootstrap admin: under another list the same
    address is whatever its row says."""
    before = policy_with(base_settings, admin_emails=[ADMIN])
    after = policy_with(base_settings, admin_emails=["grace@ethz.ch"])

    assert before.effective_role(ADMIN, UserRole.USER) is UserRole.ADMIN
    assert after.effective_role(ADMIN, UserRole.USER) is UserRole.USER
    assert after.effective_role(ADMIN, UserRole.BARISTA) is UserRole.BARISTA
    assert after.effective_role("grace@ethz.ch", UserRole.USER) is UserRole.ADMIN
