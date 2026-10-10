"""The environment of the deployed stack (SPEC A.10): `scripts/production_env.py` and
`infra/deploy/env.production`.

The VM's `.env` is rendered on the VM from three layers: `.example.env`, the committed
production values, and the VM's own file with the two Postgres passwords and the role
lists. These tests render it from the committed files and a made-up own file; nothing
here touches Docker or a network.
"""

import ast
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from app.core.config import REPO_ROOT, Settings
from app.core.identity import portal_address
from scripts import production_env
from scripts.production_env import (
    ADMIN_VARIABLE,
    ALLOWED_DOMAINS_VARIABLE,
    BARISTA_VARIABLE,
    OWN_VARIABLES,
    PASSWORD_VARIABLES,
    ROLE_VARIABLES,
    SINGLE_ADMIN_WARNING,
    EnvError,
    assignments,
    csv_entries,
    main,
    own_values,
    portal_id,
    render,
    warnings,
)

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"
PRODUCTION_ENV_FILE: Final[Path] = REPO_ROOT / "infra" / "deploy" / "env.production"
EXAMPLE: Final[str] = EXAMPLE_ENV_FILE.read_text(encoding="utf-8")
PRODUCTION: Final[str] = PRODUCTION_ENV_FILE.read_text(encoding="utf-8")

PUBLIC_ORIGIN: Final[str] = "https://09.hackathon.ethz.ch"
CSP_VARIABLE: Final[str] = "NGINX_CONTENT_SECURITY_POLICY"
VOLUME: Final[str] = "marketh_postgres-data"

DEVELOPMENT_IDS: Final[list[str]] = [
    entry for key in ROLE_VARIABLES for entry in csv_entries(assignments(EXAMPLE)[key])
]
"""The admin and the barista of a laptop. Their ids are in a committed file."""

LONGEST_LOCAL_PART: Final[str] = "a" * 64
KELVIN_SIGN: Final[str] = "\u212a"
"""Not an ASCII letter, and `str.lower` makes an ASCII "k" of it."""

NOT_AN_ID: Final[list[str]] = [
    "someone@uzh.ch",
    "mia@student.ethz.ch",
    "ada@ethz.ch.example.org",
    "ada@sub.ethz.ch",
    "no-domain",
    "@ethz.ch",
    "two@@ethz.ch",
    "ada@ethz.ch@ethz.ch",
    "a b@ethz.ch",
    ".ada@ethz.ch",
    "-ada@ethz.ch",
    "ada+vis@ethz.ch",
    "ada/vis@ethz.ch",
    f"{LONGEST_LOCAL_PART}a@ethz.ch",
    f"{KELVIN_SIGN}ate@ethz.ch",
    "zo\u00eb@ethz.ch",
]
"""Entries the portal could never send as the id of a person MarkETH lets in."""

AN_ID: Final[list[str]] = [
    "ada@ethz.ch",
    "a@ethz.ch",
    "7zip@ethz.ch",
    "first.last-2_x@ethz.ch",
    f"{LONGEST_LOCAL_PART}@ethz.ch",
]


def no_database() -> bool:
    return False


def a_database() -> bool:
    return True


def own_file(admins: str = "ada@ethz.ch,grace@ethz.ch", baristas: str = "") -> str:
    """A made-up own file, as the first run on a machine without a database writes it."""
    return own_values(None, admins, baristas, database_exists=no_database)


def with_value(own: str, key: str, value: str) -> str:
    return "".join(
        f"{key}={value}\n" if line.startswith(f"{key}=") else f"{line}\n"
        for line in own.splitlines()
    )


def without(own: str, key: str) -> str:
    return "".join(f"{line}\n" for line in own.splitlines() if not line.startswith(f"{key}="))


@pytest.fixture
def no_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` asks Docker whether the database volume exists; here it does not."""
    monkeypatch.setattr(production_env, "docker_volume_exists", lambda name: False)


@pytest.fixture
def a_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(production_env, "docker_volume_exists", lambda name: True)


def run(tmp_path: Path, *extra: str) -> int:
    return main(
        [
            "--example",
            str(EXAMPLE_ENV_FILE),
            "--production",
            str(PRODUCTION_ENV_FILE),
            "--own",
            str(tmp_path / "marketh.env"),
            "--output",
            str(tmp_path / ".env"),
            "--postgres-volume",
            VOLUME,
            *extra,
        ]
    )


# --- The rendered environment -------------------------------------------------------


def test_the_rendered_environment_has_exactly_the_keys_of_the_example() -> None:
    rendered = render(EXAMPLE, PRODUCTION, own_file())

    assert list(assignments(rendered)) == list(assignments(EXAMPLE))


def test_the_rendered_environment_keeps_the_comments_of_the_example() -> None:
    rendered = render(EXAMPLE, PRODUCTION, own_file())

    def comments(text: str) -> list[str]:
        return [line for line in text.splitlines() if "=" not in line.partition("#")[0]]

    assert comments(rendered) == comments(EXAMPLE)


def test_the_rendered_environment_loads_as_production_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("APP_ENV", "PUBLIC_BASE_URL", "ALLOWED_ORIGINS", *ROLE_VARIABLES):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(ALLOWED_DOMAINS_VARIABLE, raising=False)
    env_file = tmp_path / ".env"
    own = own_file(admins="ada@ethz.ch,grace@ethz.ch", baristas="")
    env_file.write_text(render(EXAMPLE, PRODUCTION, own), encoding="utf-8")

    settings = Settings.load(env_file)

    assert settings.app.is_production
    assert settings.app.public_base_url == PUBLIC_ORIGIN
    assert settings.app.allowed_origins == [PUBLIC_ORIGIN]
    assert settings.identity.allowed_email_domains == ["ethz.ch"]
    assert settings.identity.admin_emails == ["ada@ethz.ch", "grace@ethz.ch"]
    assert settings.identity.barista_emails == []


def test_the_own_file_wins_over_the_example() -> None:
    own = own_file(admins="ada@ethz.ch,grace@ethz.ch", baristas="linus@ethz.ch")

    rendered = assignments(render(EXAMPLE, PRODUCTION, own))

    assert rendered[ADMIN_VARIABLE] == "ada@ethz.ch,grace@ethz.ch"
    assert rendered[BARISTA_VARIABLE] == "linus@ethz.ch"
    for key in PASSWORD_VARIABLES:
        assert rendered[key] == assignments(own)[key]
        assert rendered[key] != assignments(EXAMPLE)[key]


def test_a_value_no_layer_sets_is_the_example_value() -> None:
    untouched = set(assignments(EXAMPLE)) - set(assignments(PRODUCTION)) - set(OWN_VARIABLES)
    rendered = assignments(render(EXAMPLE, PRODUCTION, own_file()))

    assert untouched
    assert {key: rendered[key] for key in untouched} == {
        key: assignments(EXAMPLE)[key] for key in untouched
    }


# --- infra/deploy/env.production ------------------------------------------------------


def test_the_production_file_holds_no_password_and_no_role_list() -> None:
    assert set(assignments(PRODUCTION)) & set(OWN_VARIABLES) == set()


def test_the_production_file_sets_only_variables_the_example_has() -> None:
    assert set(assignments(PRODUCTION)) <= set(assignments(EXAMPLE))


def test_production_is_served_from_one_https_origin() -> None:
    production = assignments(PRODUCTION)

    assert production["APP_ENV"] == "production"
    assert production["PUBLIC_BASE_URL"] == PUBLIC_ORIGIN
    assert production["ALLOWED_ORIGINS"] == PUBLIC_ORIGIN


def test_production_says_itself_that_it_listens_on_every_interface() -> None:
    """A laptop publishes the stack on its loopback interface alone, because the
    development proxy names a caller to whoever reaches it. The VM has to be reached by
    the portal, and says so in its own file instead of taking a value over."""
    assert assignments(EXAMPLE)["NGINX_HTTP_BIND"] == "127.0.0.1"
    assert assignments(PRODUCTION)["NGINX_HTTP_BIND"] == "0.0.0.0"


def test_production_lets_in_ids_of_the_one_eth_domain() -> None:
    assert assignments(PRODUCTION)[ALLOWED_DOMAINS_VARIABLE] == "ethz.ch"


def test_production_sizes_the_web_container_for_the_built_server() -> None:
    assert assignments(PRODUCTION)["WEB_MEMORY_LIMIT"] == "512m"
    assert assignments(EXAMPLE)["WEB_MEMORY_LIMIT"] != "512m", "sized for `next dev`"


def test_production_names_no_development_identity() -> None:
    production = assignments(PRODUCTION)

    assert production["DEV_USER_ID"] == ""
    assert production["DEV_USER_NAME"] == ""


def test_the_production_policy_is_the_development_one_without_eval_and_websockets() -> None:
    development = assignments(EXAMPLE)[CSP_VARIABLE]
    production = assignments(PRODUCTION)[CSP_VARIABLE]

    assert production == development.replace(" 'unsafe-eval'", "").replace(" ws: wss:", "")
    assert "'unsafe-eval'" not in production
    assert "ws:" not in production
    assert "frame-ancestors 'none'" in production
    assert "object-src 'none'" in production


# --- What the renderer refuses --------------------------------------------------------


def test_a_production_key_the_example_does_not_have_is_refused() -> None:
    with pytest.raises(EnvError, match="RENAMED_SINCE"):
        render(EXAMPLE, PRODUCTION + "RENAMED_SINCE=1\n", own_file())


def test_an_own_key_the_example_does_not_have_is_refused() -> None:
    with pytest.raises(EnvError, match="RENAMED_SINCE"):
        render(EXAMPLE, PRODUCTION, own_file() + "RENAMED_SINCE=1\n")


def test_an_own_key_that_is_not_one_of_the_four_is_refused() -> None:
    with pytest.raises(EnvError, match="APP_ENV"):
        render(EXAMPLE, PRODUCTION, own_file() + "APP_ENV=development\n")


@pytest.mark.parametrize("key", OWN_VARIABLES)
def test_a_password_or_role_list_in_the_production_file_is_refused(key: str) -> None:
    with pytest.raises(EnvError, match=key):
        render(EXAMPLE, PRODUCTION + f"{key}=x@ethz.ch\n", own_file())


@pytest.mark.parametrize("key", [*PASSWORD_VARIABLES, ADMIN_VARIABLE])
def test_an_empty_own_value_is_refused(key: str) -> None:
    with pytest.raises(EnvError, match=key):
        render(EXAMPLE, PRODUCTION, with_value(own_file(), key, ""))


@pytest.mark.parametrize("key", OWN_VARIABLES)
def test_an_own_file_without_one_of_its_four_keys_is_refused(key: str) -> None:
    """A key the own file lacks would keep the value `.example.env` has for it."""
    with pytest.raises(EnvError, match=f"has no {key}"):
        render(EXAMPLE, PRODUCTION, without(own_file(), key))


@pytest.mark.parametrize("key", ROLE_VARIABLES)
def test_a_role_list_the_own_file_lacks_is_never_filled_from_the_example(key: str) -> None:
    """The case the rule above exists for: `.example.env` names an admin and a barista."""
    assert csv_entries(assignments(EXAMPLE)[key])

    with pytest.raises(EnvError, match=key):
        render(EXAMPLE, PRODUCTION, without(own_file(baristas="linus@ethz.ch"), key))


@pytest.mark.parametrize("key", PASSWORD_VARIABLES)
def test_a_development_password_left_in_place_is_refused(key: str) -> None:
    own = with_value(own_file(), key, assignments(EXAMPLE)[key])

    with pytest.raises(EnvError, match=key):
        render(EXAMPLE, PRODUCTION, own)


@pytest.mark.parametrize("key", ROLE_VARIABLES)
@pytest.mark.parametrize("entry", NOT_AN_ID)
def test_a_role_entry_that_is_no_id_is_refused(key: str, entry: str) -> None:
    """`[a-z0-9][a-z0-9._-]{0,63}@<an allowed domain>`, the rule the API reads an id by.
    The API does not start with an entry that fails it."""
    own = with_value(own_file(), key, f"ada@ethz.ch,{entry}")

    with pytest.raises(EnvError, match=f"{key}: .* is not an id"):
        render(EXAMPLE, PRODUCTION, own)


@pytest.mark.parametrize("key", ROLE_VARIABLES)
@pytest.mark.parametrize("entry", AN_ID)
def test_a_role_entry_that_is_an_id_is_rendered_as_it_stands(key: str, entry: str) -> None:
    own = with_value(own_file(), key, f"grace@ethz.ch,{entry}")

    rendered = assignments(render(EXAMPLE, PRODUCTION, own))

    assert rendered[key] == f"grace@ethz.ch,{entry}"


@pytest.mark.parametrize("entry", [*NOT_AN_ID, *AN_ID, "Ada@ETHZ.ch", " ada@ethz.ch", ""])
def test_a_role_entry_is_read_the_way_the_api_reads_an_id(entry: str) -> None:
    """The script cannot import the API's rule (it runs on the VM's bare python3), so
    it has a copy. Here the two read every sample alike, over one and over two domains."""
    for allowed in (["ethz.ch"], ["ethz.ch", "student.ethz.ch"]):
        assert portal_id(entry, allowed) == portal_address(entry, allowed)


@pytest.mark.parametrize("key", ROLE_VARIABLES)
@pytest.mark.parametrize("development_id", DEVELOPMENT_IDS)
def test_a_role_entry_of_the_example_env_is_refused(key: str, development_id: str) -> None:
    """The admin and the barista of a laptop are no staff of the deployed stack. Their
    ids are public, and behind the portal each is whoever holds that ETH username."""
    for spelling in (development_id, development_id.upper()):
        own = with_value(own_file(), key, f"ada@ethz.ch,{spelling}")

        with pytest.raises(EnvError, match=f"{key}: .* is a development id"):
            render(EXAMPLE, PRODUCTION, own)


def test_no_rendered_role_entry_is_one_of_the_example_env() -> None:
    rendered = assignments(render(EXAMPLE, PRODUCTION, own_file(baristas="linus@ethz.ch")))

    for key in ROLE_VARIABLES:
        assert csv_entries(rendered[key])
        assert not set(csv_entries(rendered[key])) & set(DEVELOPMENT_IDS), key


def test_no_barista_is_accepted() -> None:
    rendered = assignments(render(EXAMPLE, PRODUCTION, own_file(baristas="")))

    assert rendered[BARISTA_VARIABLE] == ""


# --- The own file -------------------------------------------------------------------


def test_the_first_run_generates_two_different_passwords() -> None:
    own = assignments(own_file())

    assert list(own) == list(OWN_VARIABLES)
    assert own["POSTGRES_PASSWORD"] != own["POSTGRES_APP_PASSWORD"]
    for key in PASSWORD_VARIABLES:
        assert len(own[key]) == 48
        assert own[key] != assignments(own_file())[key], "every machine gets its own"


def test_the_first_run_needs_an_admin() -> None:
    with pytest.raises(EnvError, match=ADMIN_VARIABLE):
        own_values(None, None, None, database_exists=no_database)


def test_no_password_is_generated_while_a_database_exists() -> None:
    """Postgres took its passwords when the volume was created; generated ones would not open it."""
    with pytest.raises(EnvError, match="POSTGRES_PASSWORD"):
        own_values(None, "ada@ethz.ch", None, database_exists=a_database)


def test_an_own_file_that_lost_a_password_is_not_repaired_while_a_database_exists() -> None:
    damaged = with_value(own_file(), "POSTGRES_APP_PASSWORD", "")

    with pytest.raises(EnvError, match="POSTGRES_APP_PASSWORD"):
        own_values(damaged, None, None, database_exists=a_database)


def test_a_complete_own_file_never_asks_whether_a_database_exists() -> None:
    def must_not_be_asked() -> bool:
        raise AssertionError("asked Docker although no password was needed")

    first = own_file()

    assert own_values(first, None, None, database_exists=must_not_be_asked) == first


def test_new_role_lists_keep_the_passwords() -> None:
    first = own_file(admins="ada@ethz.ch", baristas="linus@ethz.ch")

    second = own_values(first, "ada@ethz.ch,grace@ethz.ch", "", database_exists=a_database)

    assert assignments(second) == assignments(first) | {
        ADMIN_VARIABLE: "ada@ethz.ch,grace@ethz.ch",
        BARISTA_VARIABLE: "",
    }


def test_role_lists_are_stored_in_lower_case_without_blanks_or_repeats() -> None:
    own = assignments(own_file(admins=" Ada@ETHZ.ch , grace@ethz.ch,ada@ethz.ch,", baristas=" "))

    assert own[ADMIN_VARIABLE] == "ada@ethz.ch,grace@ethz.ch"
    assert own[BARISTA_VARIABLE] == ""


def test_a_letter_from_outside_ascii_is_not_made_into_an_ascii_one() -> None:
    """`str.lower` turns the Kelvin sign into "k". The own file keeps such an entry as
    it was typed, and the renderer refuses it."""
    typed = f"{KELVIN_SIGN}ate@ethz.ch"
    assert typed.lower() == "kate@ethz.ch"

    own = own_file(admins=f"ada@ethz.ch,{typed}")

    assert assignments(own)[ADMIN_VARIABLE] == f"ada@ethz.ch,{typed}"
    with pytest.raises(EnvError, match=ADMIN_VARIABLE):
        render(EXAMPLE, PRODUCTION, own)


def test_an_own_file_with_a_foreign_key_is_refused() -> None:
    with pytest.raises(EnvError, match="APP_ENV"):
        own_values(own_file() + "APP_ENV=development\n", None, None, database_exists=no_database)


def test_an_own_file_that_lost_its_baristas_is_completed_with_none() -> None:
    """Rendering through the command never meets a missing key: the own file is written
    with all four first, and a role list it did not hold is empty, not the example's."""
    kept = without(own_file(baristas="linus@ethz.ch"), BARISTA_VARIABLE)

    completed = own_values(kept, None, None, database_exists=a_database)

    assert list(assignments(completed)) == list(OWN_VARIABLES)
    assert assignments(completed)[BARISTA_VARIABLE] == ""


# --- Warnings -----------------------------------------------------------------------


def test_a_single_admin_is_warned_about() -> None:
    """An admin who traded a market may not settle it (SPEC A.4)."""
    rendered = render(EXAMPLE, PRODUCTION, own_file(admins="ada@ethz.ch"))

    assert warnings(rendered) == [SINGLE_ADMIN_WARNING]


def test_two_admins_are_not_warned_about() -> None:
    assert warnings(render(EXAMPLE, PRODUCTION, own_file())) == []


# --- The command ---------------------------------------------------------------------


@pytest.mark.usefixtures("no_volume")
def test_the_command_creates_the_own_file_and_the_environment_for_their_owner_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--admins", "ada@ethz.ch,grace@ethz.ch") == 0

    own_path, output_path = tmp_path / "marketh.env", tmp_path / ".env"
    for path in (own_path, output_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    own = assignments(own_path.read_text(encoding="utf-8"))
    rendered = assignments(output_path.read_text(encoding="utf-8"))
    assert rendered["APP_ENV"] == "production"
    assert {key: rendered[key] for key in OWN_VARIABLES} == own
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env", "marketh.env"]

    printed = capsys.readouterr()
    for key in PASSWORD_VARIABLES:
        assert own[key] not in printed.out + printed.err
    assert "ada@ethz.ch" not in printed.out + printed.err
    assert printed.err == ""


@pytest.mark.usefixtures("no_volume")
def test_running_the_command_again_keeps_both_passwords(tmp_path: Path) -> None:
    assert run(tmp_path, "--admins", "ada@ethz.ch,grace@ethz.ch") == 0
    first = (tmp_path / "marketh.env").read_text(encoding="utf-8")

    assert run(tmp_path) == 0

    assert (tmp_path / "marketh.env").read_text(encoding="utf-8") == first
    assert stat.S_IMODE((tmp_path / "marketh.env").stat().st_mode) == 0o600


@pytest.mark.usefixtures("no_volume")
def test_the_command_warns_about_a_single_admin_and_still_renders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--admins", "ada@ethz.ch") == 0

    assert (tmp_path / ".env").is_file()
    assert capsys.readouterr().err.strip() == SINGLE_ADMIN_WARNING


@pytest.mark.usefixtures("a_volume")
def test_the_command_refuses_to_invent_passwords_for_an_existing_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "--admins", "ada@ethz.ch,grace@ethz.ch") == 2

    assert list(tmp_path.iterdir()) == []
    assert "Postgres volume exists" in capsys.readouterr().err


@pytest.mark.usefixtures("a_volume")
def test_the_command_renders_beside_an_existing_database_from_a_kept_own_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "marketh.env").write_text(own_file(), encoding="utf-8")

    assert run(tmp_path) == 0
    assert (tmp_path / ".env").is_file()


@pytest.mark.usefixtures("no_volume")
@pytest.mark.parametrize(
    "admins", ["someone@uzh.ch", DEVELOPMENT_IDS[0], "ada@ethz.ch,Ada+x@ethz.ch"]
)
def test_a_refused_environment_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], admins: str
) -> None:
    assert run(tmp_path, "--admins", admins) == 2

    assert list(tmp_path.iterdir()) == []
    assert ADMIN_VARIABLE in capsys.readouterr().err


@pytest.mark.usefixtures("no_volume")
def test_the_command_refuses_a_development_barista(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    [barista] = csv_entries(assignments(EXAMPLE)[BARISTA_VARIABLE])

    assert run(tmp_path, "--admins", "ada@ethz.ch,grace@ethz.ch", "--baristas", barista) == 2

    assert list(tmp_path.iterdir()) == []
    assert BARISTA_VARIABLE in capsys.readouterr().err


def test_docker_is_asked_for_exactly_the_named_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []

    class Listing:
        returncode = 0
        stdout = f"{VOLUME}\n"
        stderr = ""

    def fake_run(command: list[str], **_: object) -> Listing:
        asked.append(command)
        return Listing()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert production_env.docker_volume_exists(VOLUME)
    assert asked == [
        ["docker", "volume", "ls", "--quiet", "--filter", r"name=^marketh_postgres\-data$"]
    ]


def test_a_docker_that_cannot_answer_is_an_error_not_a_no(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_docker(command: list[str], **_: object) -> object:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", no_docker)

    with pytest.raises(EnvError, match="Cannot ask Docker"):
        production_env.docker_volume_exists(VOLUME)


def test_the_script_needs_only_the_standard_library() -> None:
    """`make deploy-env` runs it with the VM's bare python3, outside any virtualenv."""
    source = Path(production_env.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import needs the package around it"
            assert node.module is not None
            imported.add(node.module.partition(".")[0])

    assert imported <= sys.stdlib_module_names
