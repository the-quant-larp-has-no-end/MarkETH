"""`EmailPolicy`: whom the portal's id stands for, and with which role (SPEC §3, A.3).

The id is an identifier another system asserts, so it is read strictly: what is not
exactly an allowed address is refused, never read as the nearest known one. An accepted
id is the account, in lower case and at the domain it came with.
"""

import pytest

from app.core.config import Settings
from app.core.enums import UserRole
from app.core.errors import ForbiddenError
from app.services.email_policy import EmailPolicy
from tests.support.settings import EXAMPLE_ENV_FILE

ADMIN = "admin@ethz.ch"
BARISTA = "barista@ethz.ch"

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
    """The policy of `.example.env`: one allowed domain."""
    return policy_with(base_settings, admin_emails=[ADMIN], barista_emails=[BARISTA])


@pytest.fixture
def two_domains(base_settings: Settings) -> EmailPolicy:
    """A policy that is open to the addresses of two domains."""
    return policy_with(
        base_settings,
        allowed_email_domains=["ethz.ch", "student.ethz.ch"],
        admin_emails=[ADMIN],
        barista_emails=[BARISTA],
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


def test_a_role_at_one_domain_is_no_role_at_the_other(two_domains: EmailPolicy) -> None:
    """`admin@ethz.ch` is in ADMIN_EMAILS; whoever the portal names
    `admin@student.ethz.ch` is somebody else."""
    assert two_domains.role_for(two_domains.identify("admin@ethz.ch")) is UserRole.ADMIN
    assert two_domains.role_for(two_domains.identify("admin@student.ethz.ch")) is UserRole.USER
    assert two_domains.role_for(two_domains.identify("barista@student.ethz.ch")) is UserRole.USER


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


# --- role_for -----------------------------------------------------------------------


def test_roles_come_from_the_two_lists(policy: EmailPolicy) -> None:
    assert policy.role_for(ADMIN) is UserRole.ADMIN
    assert policy.role_for(BARISTA) is UserRole.BARISTA
    assert policy.role_for("student@ethz.ch") is UserRole.USER


def test_every_address_of_the_configured_lists_gets_its_role(base_settings: Settings) -> None:
    """The lists of `.example.env`, and the ones the tests run with in their place, are
    written so that the policy can read them."""
    for settings in (Settings.load(EXAMPLE_ENV_FILE), base_settings):
        identity = settings.identity
        policy = EmailPolicy(settings)

        assert identity.admin_emails, "an admin is named"
        for address in identity.admin_emails:
            assert policy.role_for(policy.identify(address)) is UserRole.ADMIN
        for address in set(identity.barista_emails) - set(identity.admin_emails):
            assert policy.role_for(policy.identify(address)) is UserRole.BARISTA
        assert policy.role_for(policy.identify("nobody.special@ethz.ch")) is UserRole.USER


def test_an_address_in_both_lists_is_an_admin(base_settings: Settings) -> None:
    both = policy_with(base_settings, admin_emails=[ADMIN], barista_emails=[ADMIN, BARISTA])

    assert both.role_for(ADMIN) is UserRole.ADMIN
    assert both.role_for(BARISTA) is UserRole.BARISTA


def test_a_list_entry_is_read_the_way_an_id_is(base_settings: Settings) -> None:
    """An entry written with capitals names the id the portal sends in any case."""
    written = policy_with(
        base_settings, admin_emails=["Ada@ETHZ.ch"], barista_emails=["BOB@ethz.ch"]
    )

    assert written.role_for(written.identify("ada@ethz.ch")) is UserRole.ADMIN
    assert written.role_for(written.identify("Bob@ethz.ch")) is UserRole.BARISTA
    assert written.role_for(written.identify("carol@ethz.ch")) is UserRole.USER


def test_a_role_belongs_to_the_id_whichever_case_the_portal_sends_it_in(
    policy: EmailPolicy,
) -> None:
    for raw in ("admin@ethz.ch", "ADMIN@ethz.ch", "Admin@ETHZ.CH"):
        assert policy.role_for(policy.identify(raw)) is UserRole.ADMIN


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
@pytest.mark.parametrize("variable", ["ADMIN_EMAILS", "BARISTA_EMAILS"])
def test_a_role_entry_the_rule_refuses_stops_the_policy_from_being_built(
    base_settings: Settings, variable: str, entry: str
) -> None:
    """Such an entry would give its role to nobody. The process does not start on it:
    `Container.build` constructs the policy."""
    lists: dict[str, object] = {"admin_emails": [ADMIN], "barista_emails": [BARISTA]}
    lists[variable.lower()] = [ADMIN, entry]

    with pytest.raises(ValueError, match=variable) as refused:
        policy_with(base_settings, **lists)

    assert repr(entry) in str(refused.value)


def test_empty_lists_make_everybody_a_user(base_settings: Settings) -> None:
    nobody = policy_with(base_settings, admin_emails=[], barista_emails=[])

    assert nobody.role_for(ADMIN) is UserRole.USER
    assert nobody.role_for(BARISTA) is UserRole.USER


def test_the_role_is_derived_and_stored_nowhere(base_settings: Settings) -> None:
    """The same address under another list is another role: nothing remembers the first."""
    before = policy_with(base_settings, admin_emails=[ADMIN], barista_emails=[])
    after = policy_with(base_settings, admin_emails=["grace@ethz.ch"], barista_emails=[ADMIN])

    assert before.role_for(ADMIN) is UserRole.ADMIN
    assert after.role_for(ADMIN) is UserRole.BARISTA
    assert after.role_for("grace@ethz.ch") is UserRole.ADMIN
