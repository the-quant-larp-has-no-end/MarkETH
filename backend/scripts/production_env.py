"""Render the `.env` of the deployed stack on the machine that runs it (SPEC A.10).

    python3 -B -m scripts.production_env \\
        --example ../.example.env --production ../infra/deploy/env.production \\
        --own ~/marketh.env --output ../.env --postgres-volume marketh_postgres-data \\
        [--admins a@ethz.ch,b@ethz.ch] [--baristas c@ethz.ch]

The result is `.example.env` with two layers on top:

1. `infra/deploy/env.production`: what production sets differently. Committed, no secret.
2. The machine's own file: the two Postgres passwords and who is admin and barista. It
   lives beside the deployed tree, is readable by its owner only and is never copied
   anywhere. This command creates it the first time and generates the passwords then.

Postgres takes its passwords when its volume is created. So a password is generated only
while no such volume exists: with a database in place and the own file gone, this
command stops instead of locking the stack out of its own data.

`--admins` and `--baristas` set the role lists; left out, the own file keeps what it has.
The first run needs `--admins`.

The role lists decide who may resolve markets and redeem vouchers, so an environment is
refused unless they are exactly what this machine was told:

* The own file holds all four of its keys. A key it lacks would keep the value of
  `.example.env`, and the admin of a laptop would be an admin here. `BARISTA_EMAILS` may
  be empty.
* Each entry is an id as the API reads one (`portal_id`).
* No entry is a role entry of `.example.env`. Those ids are public, and what they stand
  for on a laptop is somebody else's account behind the portal.

Standard library only: `make deploy-env` runs it with the machine's bare `python3`. It
prints no value of any variable. Exit status 2 and a message on stderr mean nothing was
written.
"""

import argparse
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Final

PASSWORD_VARIABLES: Final[tuple[str, ...]] = ("POSTGRES_PASSWORD", "POSTGRES_APP_PASSWORD")
ADMIN_VARIABLE: Final[str] = "ADMIN_EMAILS"
BARISTA_VARIABLE: Final[str] = "BARISTA_EMAILS"
ROLE_VARIABLES: Final[tuple[str, ...]] = (ADMIN_VARIABLE, BARISTA_VARIABLE)
OWN_VARIABLES: Final[tuple[str, ...]] = (*PASSWORD_VARIABLES, *ROLE_VARIABLES)
"""The own file holds exactly these, all of them, and `env.production` none of them."""
NEVER_EMPTY_OWN_VARIABLES: Final[tuple[str, ...]] = (*PASSWORD_VARIABLES, ADMIN_VARIABLE)
"""`BARISTA_EMAILS` may be empty: an admin can redeem vouchers too."""
ALLOWED_DOMAINS_VARIABLE: Final[str] = "ALLOWED_EMAIL_DOMAINS"

PASSWORD_BYTES: Final[int] = 24
OWNER_ONLY: Final[int] = 0o600

OWN_FILE_HEADER: Final[str] = (
    "# What only this machine knows about MarkETH: the two Postgres passwords and who is\n"
    "# admin and barista. Written by backend/scripts/production_env.py, which also merges\n"
    "# it into the stack's .env. Postgres took the passwords when its volume was created:\n"
    "# changing them here locks the stack out of its database.\n"
)

SINGLE_ADMIN_WARNING: Final[str] = (
    f"Warning: {ADMIN_VARIABLE} has a single address. An admin who traded a market may not "
    "resolve or void it, so a market that account trades can be settled by nobody. "
    "Add a second admin with --admins."
)

_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ID_LOCAL_PART: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
"""What stands before the `@` of an id (SPEC §3)."""


class EnvError(Exception):
    """The environment cannot be rendered; the message says what to do about it."""


def assignments(text: str) -> dict[str, str]:
    """`KEY -> value` of an env file, each value as written, quotes included."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        key, value = match.groups()
        if key in found:
            raise EnvError(f"{key} is assigned twice.")
        found[key] = value
    return found


def csv_entries(value: str) -> list[str]:
    """The entries of a comma-separated value, without surrounding quotes or blanks."""
    unquoted = value.strip()
    if len(unquoted) >= 2 and unquoted[0] == unquoted[-1] and unquoted[0] in "\"'":
        unquoted = unquoted[1:-1]
    return [entry.strip() for entry in unquoted.split(",") if entry.strip()]


def portal_id(entry: str, allowed_domains: Collection[str]) -> str | None:
    """The id a role entry stands for, or None when the portal could never send it.

    The rule is the one the API reads an id with (`app.core.identity.portal_address`,
    which this script cannot import: it runs without the API's dependencies). An id is
    ASCII and, in lower case, exactly

        [a-z0-9][a-z0-9._-]{0,63}@<one of allowed_domains>

    Nothing is trimmed and no domain is rewritten onto another. `allowed_domains` are
    lower-case domains.
    """
    # ASCII first: `str.lower` also folds characters from outside it onto ASCII letters
    # (the Kelvin sign becomes "k").
    if not entry.isascii():
        return None
    local, _, domain = entry.lower().partition("@")
    if domain not in allowed_domains or _ID_LOCAL_PART.fullmatch(local) is None:
        return None
    return f"{local}@{domain}"


def role_list(value: str) -> str:
    """A role list as the own file stores it: lower case, each entry once, no blanks.

    An entry with a character outside ASCII is kept as it was typed, for `render` to
    refuse: lower case would turn some of those characters into ASCII letters.
    """
    entries = (entry.lower() if entry.isascii() else entry for entry in csv_entries(value))
    return ",".join(dict.fromkeys(entries))


def own_values(
    existing: str | None,
    admins: str | None,
    baristas: str | None,
    *,
    database_exists: Callable[[], bool],
) -> str:
    """The text of the own file: what `existing` holds, with the role lists given here.

    A password the file does not hold is generated, unless `database_exists()`: that
    database was created with a password this command cannot know.
    """
    current = assignments(existing) if existing is not None else {}
    unknown = sorted(set(current) - set(OWN_VARIABLES))
    if unknown:
        raise EnvError(f"The own file holds {', '.join(unknown)}; it may hold only its four keys.")

    values: dict[str, str] = {}
    for key in PASSWORD_VARIABLES:
        password = current.get(key, "")
        if not password:
            if database_exists():
                raise EnvError(
                    f"{key} is not in the own file, but a Postgres volume exists. The database "
                    "was created with a password this command cannot know. Put the own file "
                    "back, or delete the database (make deploy-wipe CONFIRM=wipe) and run again."
                )
            password = secrets.token_hex(PASSWORD_BYTES)
        values[key] = password
    values[ADMIN_VARIABLE] = role_list(
        admins if admins is not None else current.get(ADMIN_VARIABLE, "")
    )
    values[BARISTA_VARIABLE] = role_list(
        baristas if baristas is not None else current.get(BARISTA_VARIABLE, "")
    )
    if not values[ADMIN_VARIABLE]:
        raise EnvError(
            f"{ADMIN_VARIABLE} is empty. Name at least one admin, by the id the portal sends "
            "for that person: --admins <eth-username>@ethz.ch[,<eth-username>@ethz.ch]"
        )
    return OWN_FILE_HEADER + "".join(f"{key}={values[key]}\n" for key in OWN_VARIABLES)


def render(example: str, production: str, own: str) -> str:
    """`example` with the values of `production` and `own` in place: same keys, same order,
    same comments."""
    example_values = assignments(example)
    production_values = assignments(production)
    own_file_values = assignments(own)

    for name, layer in (("env.production", production_values), ("The own file", own_file_values)):
        stale = sorted(set(layer) - set(example_values))
        if stale:
            raise EnvError(f"{name} sets {', '.join(stale)}, which .example.env does not have.")
    misplaced = sorted(set(production_values) & set(OWN_VARIABLES))
    if misplaced:
        raise EnvError(
            f"env.production sets {', '.join(misplaced)}; passwords and role lists belong to "
            "the own file of the machine."
        )
    extra = sorted(set(own_file_values) - set(OWN_VARIABLES))
    if extra:
        raise EnvError(f"The own file sets {', '.join(extra)}; it may hold only its four keys.")
    missing = [key for key in OWN_VARIABLES if key not in own_file_values]
    if missing:
        raise EnvError(
            f"The own file has no {', '.join(missing)}. It has to hold all four of its keys "
            f"({BARISTA_VARIABLE} may be empty): one it lacks would keep the development "
            "value of .example.env."
        )
    for key in NEVER_EMPTY_OWN_VARIABLES:
        if not own_file_values[key]:
            raise EnvError(f"{key} is empty in the own file.")
    for key in PASSWORD_VARIABLES:
        if own_file_values[key] == example_values[key]:
            raise EnvError(f"{key} is the development password of .example.env.")

    rendered = example_values | production_values | own_file_values
    if ALLOWED_DOMAINS_VARIABLE not in rendered:
        raise EnvError(f".example.env has no {ALLOWED_DOMAINS_VARIABLE}.")
    allowed_domains = sorted(
        {domain.lower() for domain in csv_entries(rendered[ALLOWED_DOMAINS_VARIABLE])}
    )
    development_ids = {
        entry.lower() for key in ROLE_VARIABLES for entry in csv_entries(example_values[key])
    }
    for key in ROLE_VARIABLES:
        for entry in csv_entries(rendered[key]):
            address = portal_id(entry, allowed_domains)
            if address is None:
                raise EnvError(
                    f"{key}: {entry!r} is not an id the portal can send: lower-case letters, "
                    "digits, '.', '_' and '-', 64 at most and the first a letter or a digit, "
                    f"then @{' or @'.join(allowed_domains)}. The API does not start with it."
                )
            if address in development_ids:
                raise EnvError(
                    f"{key}: {entry!r} is a development id of .example.env. Name the person "
                    "by the id the portal sends for them."
                )

    lines: list[str] = []
    for line in example.splitlines():
        match = _ASSIGNMENT.match(line)
        lines.append(line if match is None else f"{match.group(1)}={rendered[match.group(1)]}")
    return "\n".join(lines) + "\n"


def warnings(rendered: str) -> list[str]:
    """What is worth saying about a rendered environment that is not an error."""
    admins = csv_entries(assignments(rendered).get(ADMIN_VARIABLE, ""))
    return [SINGLE_ADMIN_WARNING] if len(admins) == 1 else []


def docker_volume_exists(name: str) -> bool:
    """Whether Docker on this machine has a volume of exactly this name."""
    try:
        listing = subprocess.run(
            ["docker", "volume", "ls", "--quiet", "--filter", f"name=^{re.escape(name)}$"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise EnvError(f"Cannot ask Docker whether the volume {name} exists: {error}") from error
    if listing.returncode != 0:
        raise EnvError(
            f"Cannot ask Docker whether the volume {name} exists: {listing.stderr.strip()}"
        )
    return bool(listing.stdout.strip())


def write_owner_only(path: Path, text: str) -> None:
    """Replace `path` in one step with a file only its owner can read."""
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(4)}.partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_ONLY)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise EnvError(f"Cannot read {what} at {path}: {error}") from error


def arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.production_env",
        description="Render the .env of the deployed stack on the machine that runs it.",
    )
    parser.add_argument("--example", type=Path, required=True, help="the .example.env of the tree")
    parser.add_argument(
        "--production", type=Path, required=True, help="infra/deploy/env.production"
    )
    parser.add_argument("--own", type=Path, required=True, help="this machine's own file")
    parser.add_argument("--output", type=Path, required=True, help="the .env to write")
    parser.add_argument(
        "--postgres-volume",
        required=True,
        help="Docker volume of the database; while it exists no password is generated",
    )
    parser.add_argument("--admins", help="comma-separated ids of the admins")
    parser.add_argument("--baristas", help="comma-separated ids of the baristas; may be empty")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    options = arguments(argv)
    own_path: Path = options.own
    output_path: Path = options.output
    try:
        example = read(options.example, ".example.env")
        production = read(options.production, "env.production")
        existing = read(own_path, "the own file") if own_path.exists() else None
        own = own_values(
            existing,
            options.admins,
            options.baristas,
            database_exists=lambda: docker_volume_exists(options.postgres_volume),
        )
        rendered = render(example, production, own)
        if own != existing:
            write_owner_only(own_path, own)
        write_owner_only(output_path, rendered)
    except EnvError as error:
        print(error, file=sys.stderr)
        return 2

    if existing is None:
        print(f"Created {own_path} with two generated passwords.")
    elif own != existing:
        print(f"Updated {own_path}.")
    rendered_values = assignments(rendered)
    print(
        f"Wrote {output_path}: {len(rendered_values)} variables, "
        f"{len(csv_entries(rendered_values[ADMIN_VARIABLE]))} admin and "
        f"{len(csv_entries(rendered_values[BARISTA_VARIABLE]))} barista addresses."
    )
    for warning in warnings(rendered):
        print(warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
