"""The stack files (SPEC §9, A.10): `compose.yaml`, `compose.dev.yaml` and the proxy
configuration under `infra/nginx/`.

The tests read the files and hold the rules whose breach nobody would notice until it
mattered: who may tell the API who is calling and who is told, which service can reach
which, that the web app's container gets no answer from the API through the proxy, who
is handed the owner role's password, that the deployed stack carries nothing of the
development one, what the proxy and the API write into their logs, that ScyllaDB is
given a container it starts in, that the proxy and the one API process may hold the
connections of a full room, that the media box reaches nothing and is told nothing, that
no video is served to a caller the API did not admit, and that every value still comes
from `.env`.

The compose files are read as text, the way they are written: two spaces per level, a
comment always on a line of its own. The nginx files are parsed into their directives.

Two tests, the last of the file, ask the image of the proxy itself: `nginx -t` on each
configuration as the stack renders it, and how many connections its own `nginx.conf`
gives a worker, each in a container of the stack's image that has no network and starts
no server. Where Docker does not run they are skipped; every other test needs nothing
but the files. The `Stack` job of `.github/workflows/ci.yml` asks nginx the same about
what Compose resolves.
"""

import ipaddress
import json
import math
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from app.api.middleware import ORIGIN_MESSAGE
from app.core.config import INFRA_ONLY_VARIABLES, REPO_ROOT
from app.core.errors import (
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnauthenticatedError,
)
from app.services.broadcasts import NOT_AVAILABLE_MESSAGE
from mediabox.settings import HLS_ROOT
from mediabox.settings import Settings as BoxSettings
from tests.support.containers import POSTGRES_IMAGE, REDIS_IMAGE, SCYLLA_COMMAND, SCYLLA_IMAGE
from tests.unit.mediabox.environment import BOX_VARIABLES

PROXY_DIRECTORY: Final[Path] = REPO_ROOT / "infra" / "nginx" / "conf.d"
DEVELOPMENT_DIRECTORY: Final[Path] = REPO_ROOT / "infra" / "nginx" / "dev"

COMPOSE: Final[str] = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
DEV_COMPOSE: Final[str] = (REPO_ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
EXAMPLE_ENV: Final[str] = (REPO_ROOT / ".example.env").read_text(encoding="utf-8")
PRODUCTION_ENV: Final[str] = (REPO_ROOT / "infra" / "deploy" / "env.production").read_text(
    encoding="utf-8"
)
MAKEFILE: Final[str] = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
CHECK: Final[str] = (REPO_ROOT / "infra" / "deploy" / "check.sh").read_text(encoding="utf-8")
DEPLOY_WORKFLOW: Final[str] = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
    encoding="utf-8"
)
FRONTEND_PACKAGE: Final[str] = (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
BACKEND_DOCKERFILE: Final[str] = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
MEDIA_DOCKERFILE: Final[str] = (REPO_ROOT / "backend" / "mediabox" / "Dockerfile").read_text(
    encoding="utf-8"
)
PROXY: Final[str] = (PROXY_DIRECTORY / "default.conf.template").read_text(encoding="utf-8")
IDENTITY: Final[str] = (DEVELOPMENT_DIRECTORY / "identity.conf.template").read_text(
    encoding="utf-8"
)
SWITCHER: Final[str] = (DEVELOPMENT_DIRECTORY / "identity.html").read_text(encoding="utf-8")

# Where the nginx image looks for templates, and the directory its own nginx.conf loads.
TEMPLATES: Final[PurePosixPath] = PurePosixPath("/etc/nginx/templates")
LOADED_BY_THE_IMAGE: Final[PurePosixPath] = PurePosixPath("/etc/nginx/conf.d")


# --- Reading .example.env -------------------------------------------------------------


def assigned(env_file: str) -> dict[str, str]:
    """Name to value of an env file as Compose reads it: surrounding quotes removed."""
    values: dict[str, str] = {}
    for name, value in re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", env_file, re.MULTILINE):
        quoted = len(value) >= 2 and value[0] == value[-1] == '"'
        values[name] = value[1:-1] if quoted else value
    return values


EXAMPLE: Final[dict[str, str]] = assigned(EXAMPLE_ENV)
DEPLOYED: Final[dict[str, str]] = EXAMPLE | assigned(PRODUCTION_ENV)
"""`.example.env` with what `infra/deploy/env.production` sets differently: the
environment of the deployed stack, apart from the passwords and the admins, which only
the VM knows."""

ID_HEADER: Final[str] = EXAMPLE["IDENTITY_HEADER_USER_ID"]
NAME_HEADER: Final[str] = EXAMPLE["IDENTITY_HEADER_USER_NAME"]
IDENTITY_HEADERS: Final[frozenset[str]] = frozenset({ID_HEADER.lower(), NAME_HEADER.lower()})
"""The two headers in which the portal names the caller, in lower case."""


def listed(variable: str) -> list[str]:
    """The entries of a comma-separated variable of `.example.env`."""
    return [entry for entry in EXAMPLE[variable].split(",") if entry]


def request_variable(header: str) -> str:
    """The nginx variable that holds a request header as the client sent it."""
    return "$http_" + header.lower().replace("-", "_")


MIB: Final[int] = 1024 * 1024


def docker_bytes(size: str) -> int:
    """A memory limit as Docker reads one: bytes, or a number of `k`, `m` or `g` in
    either case, with or without a `b` behind it. The units count in powers of 1024."""
    found = re.fullmatch(r"(\d+)(?:([kmg])b?|b)?", size.lower())
    assert found is not None, f"{size} is not a memory limit of Docker"
    number, unit = found.groups()
    return int(number) << 10 * "bkmg".index(unit or "b")


def scylla_bytes(size: str) -> int:
    """A size as ScyllaDB reads its `--memory`: bytes, or a number of `M` or `G`.

    The letter is a capital one. Docker's spelling of the same size, `2g`, ends ScyllaDB
    at once ("Could not initialize seastar: ... bad lexical cast"), and so does `2048m`:
    both tried with the image of the stack.
    """
    found = re.fullmatch(r"(\d+)([MG]?)", size)
    assert found is not None, f"ScyllaDB does not start with --memory {size}: write 2G or 2048M"
    number, unit = found.groups()
    return int(number) * {"": 1, "M": MIB, "G": 1024 * MIB}[unit]


# --- Reading the compose files --------------------------------------------------------

REFERENCE: Final[re.Pattern[str]] = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}")
SET_BY_COMPOSE: Final[frozenset[str]] = frozenset({"COMPOSE_PROJECT_NAME"})
"""Compose derives it from the `name:` of the project; it is no tunable of ours."""

BIND_MOUNT: Final[re.Pattern[str]] = re.compile(
    r"^      - type: bind\n"
    r"        source: (\S+)\n"
    r"        target: (\S+)\n"
    r"        read_only: true\n"
    r"        bind:\n"
    r"          create_host_path: false\n",
    re.MULTILINE,
)


def code_of(text: str) -> str:
    """A file without its comment lines."""
    kept = (line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return "\n".join(kept) + "\n"


def references(compose: str) -> list[tuple[str, str]]:
    """Every `${NAME...}` of a compose file as (name, what follows the name)."""
    return REFERENCE.findall(code_of(compose))


def anchor_of(compose: str, name: str) -> str:
    """The text of a top-level `x-<name>: &<name>` block."""
    found = re.search(rf"^x-{name}: &{name}\n((?:  .*\n)+)", code_of(compose), re.MULTILINE)
    assert found is not None, name
    return found.group(1)


def services_of(compose: str) -> dict[str, str]:
    """Service name to the text of that service."""
    after = code_of(compose).split("\nservices:\n", 1)[1]
    block = re.split(r"^(?=\S)", after, maxsplit=1, flags=re.MULTILINE)[0]
    pieces = re.split(r"^  ([a-z][a-z0-9-]*):\n", block, flags=re.MULTILINE)
    return dict(zip(pieces[1::2], pieces[2::2], strict=True))


def entries(service: str, key: str) -> list[str]:
    """The items of the list a service holds under `key`, without quotes."""
    found = re.search(rf"^    {key}:\n((?:      .*\n)+)", service, re.MULTILINE)
    if found is None:
        return []
    items: list[str] = re.findall(r"^      - (.*)$", found.group(1), re.MULTILINE)
    return [item.strip("\"'") for item in items]


def scalar(service: str, key: str) -> str:
    """The value a service holds under `key`."""
    found = re.search(rf"^    {key}: (.+)$", service, re.MULTILINE)
    assert found is not None, key
    return found.group(1)


STACK_VOLUME: Final[re.Pattern[str]] = re.compile(
    r"^      - ([a-z][a-z0-9-]*):(/[^:\s]+)(:ro)?$", re.MULTILINE
)
"""A volume of the stack as a service mounts it: its name, where, and whether read-only."""


def volumes_of(service: str) -> dict[str, tuple[PurePosixPath, bool]]:
    """Volume name to (container path, read-only) of the volumes of the stack a service
    mounts. A service without any has none."""
    found = re.search(r"^    volumes:\n((?:      .*\n)+)", service, re.MULTILINE)
    if found is None:
        return {}
    return {
        name: (PurePosixPath(target), read_only == ":ro")
        for name, target, read_only in STACK_VOLUME.findall(found[1])
    }


def mounts_of(service: str) -> dict[str, PurePosixPath]:
    """Host path to container path of the bind mounts of a service.

    Each of them has to be a read-only bind mount that never creates its source: a path
    that is missing then stops the container instead of mounting an empty directory.
    Whatever else the service mounts is a volume of the stack (`volumes_of`).
    """
    found = re.search(r"^    volumes:\n((?:      .*\n)+)", service, re.MULTILINE)
    assert found is not None
    mounts = {source: PurePosixPath(target) for source, target in BIND_MOUNT.findall(found[1])}
    mounted = len(mounts) + len(volumes_of(service))
    assert mounted == len(re.findall(r"^      - ", found[1], re.MULTILINE)), found[1]
    return mounts


def passed_to_nginx(service: str) -> set[str]:
    """The NGINX_* variables a compose file hands to the nginx container."""
    return set(re.findall(r"^      (NGINX_[A-Z0-9_]+):", service, re.MULTILINE))


def handed_to_nginx(service: str, environment: Mapping[str, str]) -> dict[str, str]:
    """NGINX_* name to value, as a compose file hands them to the nginx container when
    Compose reads `environment` as its `.env`."""
    return {
        name: REFERENCE.sub(lambda reference: environment[reference.group(1)], value)
        for name, value in re.findall(r"^      (NGINX_[A-Z0-9_]+): (.+)$", service, re.MULTILINE)
    }


def network_definitions(compose: str) -> dict[str, str]:
    """Network name to the text a compose file holds under it, in its top-level
    `networks:` block. A network that is left to Docker has no text."""
    found = re.search(r"^networks:\n((?:  .*\n)+)", code_of(compose), re.MULTILINE)
    if found is None:
        return {}
    pieces = re.split(r"^  ([a-z][a-z0-9-]*):\n", found.group(1), flags=re.MULTILINE)
    return dict(zip(pieces[1::2], pieces[2::2], strict=True))


def networks_of(compose: str) -> list[str]:
    """The networks a compose file defines, in its top-level `networks:` block."""
    return list(network_definitions(compose))


STACK: Final[dict[str, str]] = services_of(COMPOSE)
OVERLAY: Final[dict[str, str]] = services_of(DEV_COMPOSE)


# --- Reading the nginx files ----------------------------------------------------------

_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"""
      \#[^\n]*                                    # a comment, to the end of its line
    | (?P<symbol>[{};])
    | (?P<quoted>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    | (?P<bare>(?:\$\{\w+\}|[^\s{};"'])+)          # ${NAME} is part of a word
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Directive:
    """One nginx directive: `name arguments;`, or `name arguments { block }`."""

    name: str
    arguments: tuple[str, ...]
    block: tuple[Directive, ...] = ()

    def walk(self) -> Iterator[Directive]:
        """This directive and everything inside its block."""
        yield self
        for inner in self.block:
            yield from inner.walk()

    def inner(self, name: str) -> list[tuple[str, ...]]:
        """The arguments of each directive of that name directly inside the block."""
        return [directive.arguments for directive in self.block if directive.name == name]


def tokens(text: str) -> Iterator[tuple[str, str]]:
    """Each token of an nginx file as (kind, text). A quoted word comes without quotes."""
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        found = _TOKEN.match(text, position)
        assert found is not None, text[position : position + 40]
        position = found.end()
        if found.lastgroup == "symbol":
            yield "symbol", found.group()
        elif found.lastgroup == "quoted":
            yield "word", found.group()[1:-1]
        elif found.lastgroup == "bare":
            yield "word", found.group()


def parse(text: str) -> tuple[Directive, ...]:
    """The directives of an nginx file, blocks nested."""
    stream = tokens(text)

    def block() -> tuple[Directive, ...]:
        directives: list[Directive] = []
        words: list[str] = []
        for kind, value in stream:
            if kind == "word":
                words.append(value)
            elif value == "}":
                break
            else:
                inside = block() if value == "{" else ()
                directives.append(Directive(words[0], tuple(words[1:]), inside))
                words = []
        assert not words, words
        return tuple(directives)

    tree = block()
    assert next(stream, None) is None, "a closing brace without its opening one"
    return tree


def all_of(tree: Iterable[Directive]) -> Iterator[Directive]:
    for directive in tree:
        yield from directive.walk()


def named(tree: Iterable[Directive], name: str) -> list[Directive]:
    """Every directive of that name, at whatever depth."""
    return [directive for directive in all_of(tree) if directive.name == name]


PLACEHOLDER: Final[re.Pattern[str]] = re.compile(r"\$\{(NGINX_[A-Z0-9_]+)\}")


def placeholders(template: str) -> set[str]:
    """The `${NGINX_*}` placeholders the nginx image fills in when it renders a template."""
    return set(PLACEHOLDER.findall(template))


def rendered(template: str, handed: Mapping[str, str]) -> str:
    """A template as the nginx image renders it: every `${NGINX_*}` placeholder replaced
    by the value the container was handed, and nothing else touched."""
    return PLACEHOLDER.sub(lambda placeholder: handed[placeholder.group(1)], template)


PROXY_TREE: Final[tuple[Directive, ...]] = parse(PROXY)
IDENTITY_TREE: Final[tuple[Directive, ...]] = parse(IDENTITY)
"""The development file is included into the server block of the proxy: its top level is
the level of that block."""
[SERVER] = [directive for directive in PROXY_TREE if directive.name == "server"]


def location_of(tree: Iterable[Directive], *arguments: str) -> Directive:
    """The one `location` with these arguments among the directives of `tree`."""
    [found] = [d for d in tree if d.name == "location" and d.arguments == arguments]
    return found


def maps_to(variable: str) -> Directive:
    """The one `map` of the proxy that defines `variable`."""
    [found] = [d for d in PROXY_TREE if d.name == "map" and d.arguments[1] == variable]
    return found


def geo_of(tree: Iterable[Directive], variable: str) -> Directive:
    """The one `geo` among the directives of `tree` that defines `variable`."""
    [found] = [d for d in tree if d.name == "geo" and d.arguments[-1] == variable]
    return found


def located(peers: Directive, address: str) -> str:
    """What a `geo` makes of an address. nginx takes the value of the most specific
    network that holds the address, wherever it stands in the block, else `default`."""
    peer = ipaddress.ip_address(address)
    [(value,)] = peers.inner("default")
    longest = -1
    for entry in peers.block:
        if entry.name == "default":
            continue
        network = ipaddress.ip_network(entry.name)
        if peer.version == network.version and peer in network and network.prefixlen > longest:
            [value] = entry.arguments
            longest = network.prefixlen
    return value


def looked_up(
    variable: str, headers: Mapping[str, str], known: Mapping[str, str] | None = None
) -> str:
    """What the maps of the proxy make of `variable` for a request with these headers.

    A header the request does not carry is the empty string, as it is for nginx. A map
    is read the way nginx reads one: a source value that is written out wins, whatever
    its case, then the first regular expression that matches, then `default`. A result
    that is a variable is looked up in turn. `known` holds the variables that have a
    value before a map is read: what the `set` lines of the server block assigned, and
    what nginx itself knows about the connection.
    """
    if known is not None and variable in known:
        return known[variable]
    sent = {request_variable(name): value for name, value in headers.items()}
    if variable.startswith("$http_"):
        return sent.get(variable, "")
    rules = maps_to(variable)
    source = looked_up(rules.arguments[0], headers, known)
    written_out = {
        rule.name.lower(): rule.arguments[0]
        for rule in rules.block
        if rule.name != "default" and not rule.name.startswith("~")
    }
    patterns = [rule for rule in rules.block if rule.name.startswith("~")]
    [(fallback,)] = rules.inner("default")
    result = written_out.get(source.lower())
    if result is None:
        result = next(
            (rule.arguments[0] for rule in patterns if re.search(rule.name[1:], source)),
            fallback,
        )
    return looked_up(result, headers, known) if result.startswith("$") else result


API_CALLER: Final[str] = "$api_caller"
"""The caller as the proxy hands them to the API: the id the portal sent, and on a laptop
the caller the development file names. The limits of the proxy count by it."""


def set_by_the_server(
    request: Mapping[str, str], development: Iterable[Directive] = ()
) -> dict[str, str]:
    """The variables of a request once it has passed the `set` lines of the server block.

    nginx runs them in the order they stand in, before it picks a location. `request`
    holds what the request brought, by nginx variable (`$http_x_user_id`,
    `$cookie_dev_user_id`); a variable nothing gave a value is the empty string.
    `development` is the development file as the image renders it, which stands where
    the server block includes it; without it the line includes nothing, as in the
    deployed stack. The one condition the two files put around a `set` at this level is
    `if ($variable = "")`.
    """
    variables = dict(request)
    included = tuple(development)

    def value_of(word: str) -> str:
        return variables.get(word, "") if word.startswith("$") else word

    def run(directives: Iterable[Directive]) -> None:
        for directive in directives:
            if directive.name == "set":
                variable, value = directive.arguments
                variables[variable] = value_of(value)
            elif directive.name == "if":
                opening, equals, compared, closing = directive.arguments
                assert (equals, compared, closing) == ("=", "", ")"), directive.arguments
                if value_of(opening.removeprefix("(")) == "":
                    run(directive.block)
            elif directive.name == "include":
                run(included)

    run(SERVER.block)
    return variables


def answer_of(location: Directive) -> tuple[int, dict[str, object]]:
    """Status and JSON body a named location of the proxy answers with.

    The body is called JSON whatever the address of the request ends in: nginx takes
    the type of a body it writes from the extension of the address where its `types`
    know one, and only then from `default_type`. The address of a playlist ends in
    `.m3u8` and that of a segment in `.ts`, so each of these locations has `types`
    with nothing in it.
    """
    assert location.inner("types") == [()], location.arguments
    assert [inside for inside in location.block if inside.name == "types" and inside.block] == []
    assert location.inner("default_type") == [("application/json",)]
    [(status, body)] = location.inner("return")
    parsed: dict[str, object] = json.loads(body)
    return int(status), parsed


def upstream_of(location: Directive) -> str:
    """The upstream a location passes its requests to, as the variable that names it,
    whatever address the location asks there."""
    [(target,)] = location.inner("proxy_pass")
    return target.removeprefix("http://").partition("/")[0]


def proxied_locations(server: Directive) -> list[tuple[Directive, list[tuple[str, ...]]]]:
    """Every location of a server block that passes requests on, each with the request
    headers nginx sets there.

    Those are the location's own `proxy_set_header` lines when it has any, otherwise the
    lines of the nearest level around it that has: nginx never adds the two up.
    """
    found: list[tuple[Directive, list[tuple[str, ...]]]] = []

    def descend(level: Directive, around: list[tuple[str, ...]]) -> None:
        headers = level.inner("proxy_set_header") or around
        if level.inner("proxy_pass"):
            found.append((level, headers))
        for inside in level.block:
            if inside.name == "location":
                descend(inside, headers)

    descend(server, [])
    return found


def sent_upstream(headers: list[tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Header name in lower case to the value nginx sends; no header may stand twice."""
    sent = {arguments[0].lower(): arguments[1:] for arguments in headers}
    assert len(sent) == len(headers), headers
    return sent


def answers_of_nginx(location: Directive) -> dict[int, Directive]:
    """Status to the named location that answers it, for what nginx itself answers in
    `location` (`error_page <status>... = @name`)."""
    named_locations = {
        candidate.arguments[0]: candidate
        for candidate in SERVER.block
        if candidate.name == "location" and candidate.arguments[0].startswith("@")
    }
    return {
        int(status): named_locations[target]
        for *statuses, equals, target in location.inner("error_page")
        for status in statuses
        if equals == "="
    }


API_LOCATIONS: Final[list[Directive]] = [
    location
    for location in SERVER.block
    if location.name == "location" and location.inner("proxy_pass") == [("http://$api_upstream",)]
]
"""The locations that pass a request to the API as it arrived: the `/api/` prefix, the
event streams and the pieces of a live stream on their way up."""

BROADCAST: Final[str] = "9f1c2b7e-4d3a-4c58-8a6f-0b1e2d3c4a5f"
"""An id as a broadcast has one, for the addresses the tests below ask about."""


def regex_location(address: str) -> Directive:
    """The one regex location of the server block that takes `address`. nginx tries them
    in the order they stand in and takes the first that matches; the tests below hold
    that each address of theirs has one."""
    [found] = [
        location
        for location in SERVER.block
        if location.name == "location"
        and location.arguments[0] == "~"
        and re.search(location.arguments[1].replace("(?<", "(?P<"), address)
    ]
    return found


STREAMS: Final[Directive] = regex_location("/api/markets/0190f1a2/stream")
CHUNKS: Final[Directive] = regex_location(f"/api/broadcasts/{BROADCAST}/chunks")
"""Where a broadcaster's browser posts the pieces of its recording."""
HLS: Final[Directive] = regex_location(f"/api/broadcasts/{BROADCAST}/hls/index.m3u8")
"""Where the proxy itself serves the playlist and the segments of a live stream."""
ACCESS: Final[Directive] = location_of(SERVER.block, "=", "/_broadcast_access")
"""The question the proxy asks the API before it serves either."""

UNDER_API: Final[list[Directive]] = [*API_LOCATIONS, HLS]
"""Every location a browser reaches under `/api/`."""


# --- compose.yaml alone is the deployed stack -----------------------------------------


def test_compose_yaml_alone_knows_nothing_of_the_development_identity() -> None:
    """Comments included: the file names nothing that exists on a laptop only."""
    deployed = COMPOSE.lower()
    for word in ("infra/nginx/dev", "templates/dev", "dev_user", "__dev", "identity"):
        assert word not in deployed, word


def test_compose_yaml_alone_knows_nothing_of_mail() -> None:
    assert re.findall(r"mailpit|smtp|\bmail", COMPOSE, re.IGNORECASE) == []


@pytest.mark.parametrize(
    "compose", [COMPOSE, DEV_COMPOSE], ids=["compose.yaml", "compose.dev.yaml"]
)
def test_every_variable_compose_reads_is_in_the_example_env_and_has_no_default(
    compose: str,
) -> None:
    """Golden rule 7: a variable that is missing stops Compose, it never falls back."""
    found = [(name, rest) for name, rest in references(compose) if name not in SET_BY_COMPOSE]
    assert found
    for name, rest in found:
        assert name in EXAMPLE, f"{name} is not in .example.env"
        assert rest == ":?", f"${{{name}{rest}}} is not ${{{name}:?}}"


def test_every_infra_only_variable_is_read_by_compose_or_by_the_makefile() -> None:
    read_by_compose = {name for name, _ in references(COMPOSE) + references(DEV_COMPOSE)}

    def read(name: str) -> bool:
        return name in read_by_compose or re.search(rf"\b{name}\b", MAKEFILE) is not None

    for name in sorted(INFRA_ONLY_VARIABLES):
        assert read(name), f"{name} is in .example.env and nothing reads it"


def test_every_service_has_a_memory_limit_from_the_env() -> None:
    for name, service in STACK.items():
        limit = scalar(service, "mem_limit")
        assert re.fullmatch(r"\$\{[A-Z_]+_MEMORY_LIMIT:\?\}", limit), f"{name}: {limit}"


SCYLLA_SETS_ASIDE: Final[int] = 1536 * MIB
"""What ScyllaDB keeps of its container's memory for everything that is not its own,
before the share of the kernel: 1.5 GiB. (Seastar, which it is built on, keeps 7% where
that is more, which is above 21 GiB; no container of this stack comes near it.)"""

ROOM_FOR_THE_KERNEL: Final[int] = 256 * MIB
"""Room for what the kernel of the host keeps free (`vm.min_free_kbytes`), which ScyllaDB
sets aside as well. How much that is, is the host's to say: 44 MiB under Docker Desktop
on the laptop the rule was measured on, 66 MiB on the VM (docs/SERVER_INFRASTRUCTURE.md
§13, check 9). 256 MiB is the ceiling of the kernel's default rule for it."""


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_scylla_container_leaves_room_for_the_memory_scylla_is_told_to_use(
    environment: Mapping[str, str],
) -> None:
    """ScyllaDB refuses to start ("insufficient physical memory: needed ... available
    ...") unless the limit of its container, less what it sets aside, is at least its
    `--memory`. Its container then stays up without it, unhealthy.

    What it sets aside is 1.5 GiB and what the kernel of the host keeps free
    (`vm.min_free_kbytes`, which its binary reads). Measured with the image of the
    stack under Docker Desktop, where the kernel keeps 44 MiB: asked for more than its
    container held, ScyllaDB named as available the limit less 1580 MiB and less 50 MiB
    per shard (one shard under limits from 2130 MiB to 6 GiB; two and four shards under
    4 GiB). What it needs is `--memory` less the same 50 MiB per shard, so the shards
    drop out of the rule. `--memory 2G` was refused under 3627 MiB and started under
    3628 MiB, which is 2 GiB and 1580 MiB.

    The kernel's share differs from host to host, and the VM is another host than the
    laptop, so the rule here allows `ROOM_FOR_THE_KERNEL` for it. It holds for
    `.example.env` and for what `infra/deploy/env.production` makes of it.
    """
    assert scalar(STACK["scylla"], "mem_limit") == "${SCYLLA_MEMORY_LIMIT:?}"
    flags = entries(STACK["scylla"], "command")
    assert flags[flags.index("--memory") + 1] == "${SCYLLA_MEMORY:?}"

    memory = scylla_bytes(environment["SCYLLA_MEMORY"])
    limit = docker_bytes(environment["SCYLLA_MEMORY_LIMIT"])
    needed = memory + SCYLLA_SETS_ASIDE + ROOM_FOR_THE_KERNEL
    assert limit >= needed, (
        f"SCYLLA_MEMORY_LIMIT={environment['SCYLLA_MEMORY_LIMIT']} is {limit // MIB} MiB, and "
        f"SCYLLA_MEMORY={environment['SCYLLA_MEMORY']} takes a limit of {needed // MIB} MiB: "
        f"its own {memory // MIB} MiB, the {SCYLLA_SETS_ASIDE // MIB} MiB ScyllaDB sets "
        f"aside and {ROOM_FOR_THE_KERNEL // MIB} MiB for what the kernel keeps free. "
        "ScyllaDB might not start"
    )


NOFILE_LIMITS: Final[dict[str, str]] = {
    "nginx": "NGINX_NOFILE_LIMIT",
    "api": "API_NOFILE_LIMIT",
}
"""The services whose open connections grow with the number of people in the room, each
with the variable of `.env` that is its limit of open files."""

NOFILE: Final[re.Pattern[str]] = re.compile(
    r"^    ulimits:\n"
    r"      nofile:\n"
    r"        soft: (\S+)\n"
    r"        hard: (\S+)\n"
    r"(?!      )",
    re.MULTILINE,
)
"""The one limit a service sets under `ulimits`: soft and hard, and nothing after them."""


def test_nginx_and_the_api_have_their_limit_of_open_files_from_the_env() -> None:
    """Soft and hard are the same variable, so the limit a process starts with is the
    limit it has. No other service sets one, the shared block of the backend services
    included: the relay and `migrate` hold a handful of connections each. The overlay
    changes neither."""
    limited = {name for name, service in STACK.items() if "ulimits" in service}
    assert limited == set(NOFILE_LIMITS)
    for name, variable in NOFILE_LIMITS.items():
        assert STACK[name].count("ulimits") == 1, name
        [(soft, hard)] = NOFILE.findall(STACK[name])
        assert soft == hard == f"${{{variable}:?}}", name
    assert "ulimits" not in anchor_of(COMPOSE, "backend")
    assert "ulimits" not in code_of(DEV_COMPOSE)


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_limits_of_open_files_cover_a_full_room(environment: Mapping[str, str]) -> None:
    """The API holds one socket per open event stream and its connections to Postgres.
    nginx is given twice the connections of a full room: two per open event stream, one
    per viewer of a live stream (the test below holds the same figure to what one
    worker takes). Each rule adds 256 files for everything else. They hold for
    `.example.env` and for what `infra/deploy/env.production` makes of it."""
    streams = int(environment["LIVE_MAX_STREAMS"])
    viewers = int(environment["BROADCAST_MAX_VIEWERS"])
    pool = int(environment["POSTGRES_POOL_SIZE"]) + int(environment["POSTGRES_MAX_OVERFLOW"])

    for_the_api = streams + pool + 256
    assert int(environment["API_NOFILE_LIMIT"]) >= for_the_api, (
        f"API_NOFILE_LIMIT={environment['API_NOFILE_LIMIT']} is below {for_the_api}: "
        "LIVE_MAX_STREAMS + POSTGRES_POOL_SIZE + POSTGRES_MAX_OVERFLOW + 256"
    )
    for_nginx = 2 * (2 * streams + viewers) + 256
    assert int(environment["NGINX_NOFILE_LIMIT"]) >= for_nginx, (
        f"NGINX_NOFILE_LIMIT={environment['NGINX_NOFILE_LIMIT']} is below {for_nginx}: "
        "4 x LIVE_MAX_STREAMS + 2 x BROADCAST_MAX_VIEWERS + 256"
    )


NGINX_IMAGE: Final[str] = "nginx:1.30-alpine"
WORKER_CONNECTIONS: Final[int] = 1024
"""`worker_connections` in the `nginx.conf` of `NGINX_IMAGE`: the connections one worker
process of the proxy holds, to clients and upstream together. The stack mounts no
`nginx.conf` of its own (it renders into `conf.d` alone), so the figure of the image is
the figure its workers run with. The two belong together: another image is another
figure, and the last test of this file reads it from the image."""


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_one_worker_of_the_proxy_has_the_connections_of_a_full_room(
    environment: Mapping[str, str],
) -> None:
    """An open event stream is two connections of the proxy, a viewer of a live stream
    one. However the kernel spreads a room over the workers, the whole room fits one of
    them with an eighth of its connections to spare. `LIVE_MAX_STREAMS` is raised only
    as far as this holds."""
    assert scalar(STACK["nginx"], "image") == NGINX_IMAGE
    streams = int(environment["LIVE_MAX_STREAMS"])
    viewers = int(environment["BROADCAST_MAX_VIEWERS"])
    needed = 2 * streams + viewers
    within = WORKER_CONNECTIONS * 7 // 8
    assert needed <= within, (
        f"2 x LIVE_MAX_STREAMS + BROADCAST_MAX_VIEWERS is {needed}, above {within}: seven "
        f"eighths of the {WORKER_CONNECTIONS} connections of one worker of {NGINX_IMAGE}"
    )


def test_every_worker_of_the_proxy_listens_on_a_socket_of_its_own() -> None:
    """`reuseport`: the kernel hands each connection that arrives to one worker's own
    socket. On a socket the workers share, connections that arrive one by one mostly go
    to the same worker, which takes no more than its `worker_connections` while the
    others idle. The server has the one `listen`, and the development file adds none."""
    assert [listen.arguments for listen in named(PROXY_TREE, "listen")] == [
        ("80", "default_server", "reuseport")
    ]
    assert SERVER.inner("listen") == [("80", "default_server", "reuseport")]
    assert named(IDENTITY_TREE, "listen") == []


UVICORN_WORKER_VARIABLES: Final[tuple[str, ...]] = ("WEB_CONCURRENCY", "UVICORN_WORKERS")
"""The variables from which uvicorn takes a number of worker processes when its command
names none. The API receives the whole of `.env`, so neither is a key of it."""


def test_one_process_serves_the_api() -> None:
    """What the API keeps in its own memory is kept once, such as the orders it counts
    per user while Redis is away (SPEC A.4): a second process would count on its own.
    So the command names no `--workers`, deployed and reloading alike, nothing in `.env`
    asks uvicorn for workers, no service is run in several copies, and no service but
    `api` runs the application. `infra/deploy/check.sh` counts the processes that
    listen in the running container (check 14)."""
    for command in (entries(STACK["api"], "command"), entries(OVERLAY["api"], "command")):
        assert command[:2] == ["uvicorn", "app.main:create_app"]
        assert not any(word.startswith("--workers") for word in command), command
    for variable in UVICORN_WORKER_VARIABLES:
        assert variable not in EXAMPLE, variable
        assert variable not in code_of(COMPOSE) + code_of(DEV_COMPOSE), variable
    for services in (STACK, OVERLAY):
        assert [name for name, service in services.items() if "app.main" in service] == ["api"]
    for compose in (COMPOSE, DEV_COMPOSE):
        for word in ("replicas", "scale:", "deploy:"):
            assert word not in code_of(compose), word


def test_every_service_rotates_its_log() -> None:
    rotation = anchor_of(COMPOSE, "logging")
    assert "  driver: json-file\n" in rotation
    assert "    max-size: ${CONTAINER_LOG_MAX_SIZE:?}\n" in rotation
    assert '    max-file: "${CONTAINER_LOG_MAX_FILES:?}"\n' in rotation
    backend_rotates = "  logging: *logging\n" in anchor_of(COMPOSE, "backend")
    for name, service in STACK.items():
        own = "    logging: *logging\n" in service
        inherited = "    <<: *backend\n" in service and backend_rotates
        assert own or inherited, name


def test_nginx_is_the_only_published_port_of_the_deployed_stack() -> None:
    assert {name for name, service in STACK.items() if "ports:" in service} == {"nginx"}
    assert entries(STACK["nginx"], "ports") == ["${NGINX_HTTP_BIND:?}:${NGINX_HTTP_PORT:?}:80"]


def test_the_overlay_publishes_its_extra_ports_on_the_loopback_interface_only() -> None:
    """Its passwords are development ones, and its proxy lets anybody be anybody."""
    assert "ports:" not in OVERLAY["nginx"]
    published = [port for service in OVERLAY.values() for port in entries(service, "ports")]
    assert published
    for port in published:
        assert port.startswith("127.0.0.1:"), port


def test_migrate_builds_the_schema_and_then_proves_it_is_the_one_of_this_code() -> None:
    """It takes the database through the revisions, and a database whose schema is still
    not the one of the models, because no revision leads there, must stop here."""
    [chain] = [entry for entry in entries(STACK["migrate"], "command") if "alembic" in entry]
    steps = chain.split(" && ")
    assert steps[0] == "alembic upgrade head"
    assert steps[-1] == "python -m scripts.check_schema"
    assert (REPO_ROOT / "backend" / "scripts" / "check_schema.py").is_file()
    assert scalar(STACK["migrate"], "restart") == '"no"'


def test_api_and_relay_start_only_after_migrate_succeeded() -> None:
    """They wait for Postgres and the schema, and for neither Redis nor ScyllaDB (§9)."""
    needs = anchor_of(COMPOSE, "needs-schema")
    assert "  postgres:\n    condition: service_healthy\n" in needs
    assert "  migrate:\n    condition: service_completed_successfully\n" in needs
    assert "redis" not in needs
    assert "scylla" not in needs
    for name in ("api", "relay"):
        assert "    depends_on: *needs-schema\n" in STACK[name], name


def test_the_api_runs_the_command_of_its_image_with_a_limit_on_a_graceful_stop() -> None:
    """An open event stream never ends by itself. Docker kills a container ten seconds
    after asking it to stop; the API has to be done before that. The limit is a tunable
    of `.env`, for the deployed command and for the reloading one of the overlay."""
    found = re.search(r"^CMD (\[.+\])$", BACKEND_DOCKERFILE, re.MULTILINE)
    assert found is not None
    image_command: list[str] = json.loads(found.group(1))
    *command, flag, seconds = entries(STACK["api"], "command")
    assert command == image_command
    assert flag == "--timeout-graceful-shutdown"
    assert seconds == "${API_SHUTDOWN_TIMEOUT_SECONDS:?}"
    assert entries(OVERLAY["api"], "command")[-2:] == [flag, seconds]
    assert 0 < int(EXAMPLE["API_SHUTDOWN_TIMEOUT_SECONDS"]) < 10


def test_the_deployed_api_writes_no_access_log_of_its_own() -> None:
    """uvicorn's access line is the whole request line, query string included, and a
    query can hold the name an admin searches for. The access log of the stack is the
    proxy's, which leaves the query out. The flag stands in the command of the image,
    which is the command the deployed stack runs (the test above holds the two equal)."""
    found = re.search(r"^CMD (\[.+\])$", BACKEND_DOCKERFILE, re.MULTILINE)
    assert found is not None
    assert "--no-access-log" in json.loads(found.group(1))
    assert "--no-access-log" in entries(STACK["api"], "command")


def test_the_web_app_shares_a_network_with_the_proxy_alone() -> None:
    """Two networks: `edge` holds nginx and web, `core` holds nginx, the API and what the
    API works with. The web app is on no network with the API or a store, so it cannot
    open a connection to any of them: Redis and ScyllaDB ask for no password, and the
    API takes its caller from two request headers. What the web app can reach is the
    proxy, which refuses it under /api/ (the tests of the edge network below)."""
    assert sorted(networks_of(COMPOSE)) == ["core", "edge"]
    joined = {name: entries(service, "networks") for name, service in STACK.items()}
    assert joined.pop("web") == ["edge"]
    assert joined.pop("nginx") == ["edge", "core"]
    assert joined.pop("media") == [], "the media box has no network at all"
    assert set(joined) == {"api", "relay", "migrate", "postgres", "redis", "scylla"}
    for name, networks in joined.items():
        assert networks == ["core"], name


EDGE_ADDRESSES: Final[str] = (
    "    ipam:\n"
    "      config:\n"
    "        - subnet: ${EDGE_SUBNET:?}\n"
    "          gateway: ${EDGE_GATEWAY:?}\n"
)
"""What `compose.yaml` holds under the network `edge`."""


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_edge_network_has_a_range_of_its_own_and_its_gateway_lies_inside(
    environment: Mapping[str, str],
) -> None:
    """The proxy refuses /api/ to a peer on `edge`, so it has to know which addresses
    those are: `compose.yaml` gives the network the range and the gateway `.env` names
    instead of leaving both to Docker, and hands the proxy the same two values. The
    range is a private IPv4 network written as one (no bit set behind its prefix), the
    gateway is one of its hosts, and besides the gateway it has room for the proxy and
    the web app. `core` is left to Docker: nothing has to know its addresses."""
    assert network_definitions(COMPOSE) == {"edge": EDGE_ADDRESSES, "core": ""}
    handed = handed_to_nginx(STACK["nginx"], environment)
    assert handed["NGINX_EDGE_SUBNET"] == environment["EDGE_SUBNET"]
    assert handed["NGINX_EDGE_GATEWAY"] == environment["EDGE_GATEWAY"]

    subnet = ipaddress.ip_network(environment["EDGE_SUBNET"])
    gateway = ipaddress.ip_address(environment["EDGE_GATEWAY"])
    assert subnet.version == 4, "nginx listens on IPv4 alone"
    assert subnet.is_private, subnet
    hosts = list(subnet.hosts())
    assert gateway in hosts, f"{gateway} is no host of {subnet}"
    on_the_network = [name for name in STACK if "edge" in entries(STACK[name], "networks")]
    assert len(hosts) - 1 >= len(on_the_network)


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_gateway_of_the_edge_network_is_a_peer_the_proxy_believes(
    environment: Mapping[str, str],
) -> None:
    """Depending on how Docker passes on what arrives on the published port, the proxy
    sees it coming from the gateway of one of its two networks. When that is the gateway
    of `edge`, the portal's request arrives from this address, and the proxy has to take
    the visitor's address and scheme from it as it does from any other private peer."""
    gateway = ipaddress.ip_address(environment["EDGE_GATEWAY"])
    believed = [d.arguments[0] for d in PROXY_TREE if d.name == "set_real_ip_from"]
    assert any(gateway in ipaddress.ip_network(network) for network in believed)
    assert located(geo_of(PROXY_TREE, "$peer_is_trusted"), str(gateway)) == "1"


def test_nothing_reaches_around_the_two_networks() -> None:
    """No service joins the host's network or another container's, none is linked to
    another, and the overlay leaves every service on the networks it has. The one
    `network_mode` of the stack takes a network away: the media box has none."""
    for compose in (COMPOSE, DEV_COMPOSE):
        for word in ("links:", "external", "extra_hosts"):
            assert word not in code_of(compose), word
    without_a_network = {name for name, service in STACK.items() if "network_mode" in service}
    assert without_a_network == {"media"}
    assert re.findall(r"network_mode.*", code_of(COMPOSE)) == ["network_mode: none"]
    assert "network_mode" not in code_of(DEV_COMPOSE)
    assert networks_of(DEV_COMPOSE) == []
    for name, service in OVERLAY.items():
        assert "networks:" not in service, name


def test_only_migrate_receives_the_password_of_the_owner_role() -> None:
    """api and relay connect as the app role, which cannot rewrite the ledger (SPEC §7).
    Each of the three gets the whole of `.env`; what is built on the shared block has
    the owner role's password blanked, and migrate alone takes its environment without
    that line."""
    stores = anchor_of(COMPOSE, "stores")
    backend = anchor_of(COMPOSE, "backend")
    assert "PASSWORD" not in stores
    assert "  env_file: .env\n" in backend
    assert '  environment:\n    <<: *stores\n    POSTGRES_PASSWORD: ""\n' in backend
    for name in ("api", "relay"):
        assert "    <<: *backend\n" in STACK[name], name
        assert "environment:" not in STACK[name], name
        assert "env_file" not in STACK[name], name
    assert "    <<: *backend\n" in STACK["migrate"]
    assert "    environment: *stores\n" in STACK["migrate"]

    for name in set(STACK) - {"api", "relay", "migrate"}:
        assert "env_file" not in STACK[name], name
        assert "*backend" not in STACK[name], name
    reading_it = {name for name, service in STACK.items() if "POSTGRES_PASSWORD" in service}
    assert reading_it == {"postgres"}, "the database itself, which is created with it"

    assert "POSTGRES_PASSWORD" not in code_of(DEV_COMPOSE)
    for name in ("api", "relay"):
        assert "environment:" not in OVERLAY[name], name
        assert "env_file" not in OVERLAY[name], name


def test_the_suite_runs_the_images_and_the_scylla_flags_of_the_stack() -> None:
    """`tests/support/containers.py` says so; SPEC §9 names the four ScyllaDB flags."""
    assert scalar(STACK["postgres"], "image") == POSTGRES_IMAGE
    assert scalar(STACK["redis"], "image") == REDIS_IMAGE
    assert scalar(STACK["scylla"], "image") == SCYLLA_IMAGE
    flags = " ".join(entries(STACK["scylla"], "command"))
    for name, _ in REFERENCE.findall(flags):
        flags = flags.replace(f"${{{name}:?}}", EXAMPLE[name])
    assert flags == SCYLLA_COMMAND


# --- The media box ----------------------------------------------------------------------

BOX_USER: Final[int] = 10001
API_USER: Final[int] = 1000
"""The users of the two images: the box's (`backend/mediabox/Dockerfile`) and the API's
(`backend/Dockerfile`). The volumes of the stack name both."""

SOCKET_VOLUME: Final[str] = "media-socket"
HLS_VOLUME: Final[str] = "media-hls"
SOCKET_DIRECTORY: Final[PurePosixPath] = PurePosixPath(EXAMPLE["BROADCAST_MEDIA_SOCKET"]).parent
"""Where `api` and `media` mount the volume on which the box listens."""
HLS_DIRECTORY: Final[PurePosixPath] = PurePosixPath(HLS_ROOT.as_posix())
"""Where the box writes playlists and segments: a constant of its code, no variable."""

MEMORY_VOLUME: Final[str] = (
    '    driver_opts:\n      type: tmpfs\n      device: tmpfs\n      o: "{options}"\n'
)
"""What `compose.yaml` holds under a volume that is memory."""

BOX_LIMITS: Final[dict[str, str]] = {
    "pids_limit": "MEDIA_PIDS_LIMIT",
    "mem_limit": "MEDIA_MEMORY_LIMIT",
    "cpus": "MEDIA_CPUS",
}
"""The ceilings of the box, each with the variable of `.env` that sets it."""


def volume_definitions(compose: str) -> dict[str, str]:
    """Volume name to the text a compose file holds under it, in its top-level
    `volumes:` block. A volume that is left to Docker has no text."""
    found = re.search(r"^volumes:\n((?:  .*\n)+)", code_of(compose), re.MULTILINE)
    assert found is not None
    pieces = re.split(r"^  ([a-z][a-z0-9-]*):\n", found.group(1), flags=re.MULTILINE)
    return dict(zip(pieces[1::2], pieces[2::2], strict=True))


def handed_to_the_box(environment: Mapping[str, str]) -> dict[str, str]:
    """Name to value of the variables `compose.yaml` hands the media box when Compose
    reads `environment` as its `.env`. Every line under its `environment:` is one."""
    found = re.search(r"^    environment:\n((?:      .*\n)+)", STACK["media"], re.MULTILINE)
    assert found is not None
    lines = found.group(1).splitlines()
    handed = dict(re.findall(r"^      ([A-Z][A-Z0-9_]*): (.+)$", found.group(1), re.MULTILINE))
    assert len(handed) == len(lines), lines
    return {
        name: REFERENCE.sub(lambda reference: environment[reference.group(1)], value)
        for name, value in handed.items()
    }


def test_the_media_box_reaches_nothing_and_may_do_nothing() -> None:
    """ffmpeg reads bytes a person chose, so the box is the container in which somebody
    may one day run code of their own. It is on no network, not even one of its own;
    its root is read-only and so is the memory disk a container gets at /dev/shm
    (`ipc: none`); it has no capability and can gain none; and its processes, its
    memory and its share of the CPUs have a ceiling from `.env`. It names no user: the
    one of its image holds, which is not root. The overlay gives it nothing, so the
    development stack runs the same box."""
    box = STACK["media"]
    assert "    build:\n      context: ./backend/mediabox\n" in box
    assert scalar(box, "network_mode") == "none"
    assert scalar(box, "read_only") == "true"
    assert scalar(box, "ipc") == "none"
    assert entries(box, "cap_drop") == ["ALL"]
    assert entries(box, "security_opt") == ["no-new-privileges:true"]
    for variable in BOX_LIMITS.values():
        assert variable in EXAMPLE, variable
    for key, variable in BOX_LIMITS.items():
        assert scalar(box, key) == f"${{{variable}:?}}", key
    for word in ("networks:", "ports:", "privileged", "cap_add", "devices:", "user:", "pid:"):
        assert word not in box, word
    assert "tmpfs:" not in box, "what it may write is its two volumes"
    assert re.search(r"^USER media$", MEDIA_DOCKERFILE, re.MULTILINE)
    assert "media" not in OVERLAY


def test_the_media_box_is_process_one_and_starts_again_empty() -> None:
    """The supervisor has to be the first process of its container: the kernel delivers
    neither SIGKILL nor SIGSTOP to that process from inside, so nothing ffmpeg is made to
    run can end or freeze it. Hence no `init`, which would take that place. The process
    ends by itself with its last live stream, and `unless-stopped` starts a container
    for the next one, whatever status it ended with. Its health check asks the socket."""
    box = STACK["media"]
    assert "init:" not in box
    assert scalar(box, "restart") == "unless-stopped"
    found = re.search(r"^CMD (\[.+\])$", MEDIA_DOCKERFILE, re.MULTILINE)
    assert found is not None
    assert json.loads(found.group(1)) == ["python", "-m", "mediabox"]
    assert "command:" not in box
    assert "entrypoint:" not in box
    assert '      test: ["CMD", "python", "-m", "mediabox.probe"]\n' in box
    assert (REPO_ROOT / "backend" / "mediabox" / "probe.py").is_file()


def test_nobody_waits_for_the_media_box() -> None:
    """Without the box the stack starts and trades, and a live stream does not start
    (SPEC §9). No service names it under `depends_on`, the shared block of the backend
    services included, and the box waits for nobody."""
    assert "depends_on" not in STACK["media"]
    assert "media" not in anchor_of(COMPOSE, "needs-schema")
    assert "media" not in anchor_of(COMPOSE, "backend")
    for name, service in STACK.items():
        waits_for = re.search(r"^    depends_on:\n((?:      .*\n)+)", service, re.MULTILINE)
        if waits_for is not None:
            assert "media" not in waits_for.group(1), name


OPTIONAL_PARTS: Final[tuple[str, ...]] = (
    "REDIS_TIMEOUT_MS",
    "SCYLLA_REQUEST_TIMEOUT_MS",
    "BROADCAST_MEDIA_TIMEOUT_MS",
)
"""How long the API waits for each part of the stack it works without, by the variable
of `.env` that says so."""


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_a_part_the_api_works_without_cannot_make_its_container_unhealthy(
    environment: Mapping[str, str],
) -> None:
    """`GET /api/health` asks Redis, ScyllaDB and the media box with every call, all at
    once, and each for at most its own timeout. A box that is there and silent
    takes the whole of `BROADCAST_MEDIA_TIMEOUT_MS`. The health check of the `api`
    container gives that request a number of seconds of its own; each of the timeouts
    is below it, so a part that does not answer shows as `down` in an answer that
    arrives, and Compose never calls the API unhealthy for it. Docker in turn gives the
    whole check more time than the request inside it takes."""
    asked = re.search(r'urlopen\("http://127\.0\.0\.1:8000/api/health", timeout=(\d+)\)', COMPOSE)
    assert asked is not None
    patience = int(asked.group(1))
    for variable in OPTIONAL_PARTS:
        assert int(environment[variable]) < patience * 1000, (
            f"{variable}={environment[variable]} is not below the {patience} seconds "
            "the health check of the api container gives GET /api/health"
        )
    limit = re.search(r"^      timeout: (\d+)s$", STACK["api"], re.MULTILINE)
    assert limit is not None
    assert int(limit.group(1)) > patience


def test_if_the_host_runs_out_of_memory_video_goes_first_and_the_balances_last() -> None:
    """`oom_score_adj`: the kernel ends the media box before ScyllaDB, which holds the
    price history, and Postgres after everything else."""
    adjusted = {
        name: int(scalar(service, "oom_score_adj"))
        for name, service in STACK.items()
        if "oom_score_adj" in service
    }
    assert set(adjusted) == {"media", "scylla", "postgres"}
    assert adjusted["media"] > adjusted["scylla"] > 0 > adjusted["postgres"]


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_media_box_is_handed_its_own_variables_and_nothing_of_the_env_file(
    environment: Mapping[str, str],
) -> None:
    """The box has no `env_file`: Compose hands it the variables its settings read, each
    from `.env` without a default, and no other. So no password of a store and no admin
    is in the environment of the one container that runs ffmpeg on bytes a person chose.
    What it is handed is what its own settings accept: a value they refuse ends the
    process, and the restart policy would start it again and again."""
    box = STACK["media"]
    assert "env_file" not in box
    assert "*backend" not in box
    assert "*stores" not in box

    written = handed_to_the_box({name: f"<{name}>" for name in environment})
    assert set(written) == set(BOX_VARIABLES)
    for name, value in written.items():
        assert value == f"<{name}>", f"{name} is handed the value of another variable"
        assert f"      {name}: ${{{name}:?}}\n" in box, name
        assert not name.startswith(("POSTGRES", "REDIS", "SCYLLA")), name
    assert "ADMIN_EMAILS" not in written
    read = {name for name, _ in REFERENCE.findall(box)}
    assert read == set(BOX_VARIABLES) | set(BOX_LIMITS.values())

    accepted = BoxSettings.from_environment(handed_to_the_box(environment))
    assert PurePosixPath(accepted.socket_path.as_posix()).parent == SOCKET_DIRECTORY


def test_the_two_volumes_of_the_media_box_are_memory_and_belong_to_its_user() -> None:
    """No picture and no sound is written to a disk: both volumes are tmpfs, and gone
    with their containers. Both belong to the user of the box. The directory of the
    socket lets the group of the API's image in and nobody else; the box tells a peer
    of its own user nothing but its status, so the two users have to differ. What the
    box writes for viewers everybody may read, which is how the proxy serves it. The
    size of that volume comes from `.env`; a socket needs none to speak of."""
    assert re.search(rf"^    && adduser -u {BOX_USER} -G media ", MEDIA_DOCKERFILE, re.MULTILINE)
    assert f"addgroup -g {BOX_USER} media" in MEDIA_DOCKERFILE
    assert f"groupadd --gid {API_USER} app" in BACKEND_DOCKERFILE
    assert f"useradd --uid {API_USER} --gid app" in BACKEND_DOCKERFILE
    assert re.findall(r"^USER (\S+)$", BACKEND_DOCKERFILE, re.MULTILINE) == ["app", "app"]
    assert BOX_USER != API_USER

    volumes = volume_definitions(COMPOSE)
    assert set(volumes) == {"postgres-data", "scylla-data", SOCKET_VOLUME, HLS_VOLUME}
    assert volumes[SOCKET_VOLUME] == MEMORY_VOLUME.format(
        options=f"size=64k,mode=0750,uid={BOX_USER},gid={API_USER}"
    )
    assert volumes[HLS_VOLUME] == MEMORY_VOLUME.format(
        options=f"size=${{MEDIA_HLS_TMPFS_SIZE:?}},mode=0755,uid={BOX_USER},gid={BOX_USER}"
    )
    assert not {SOCKET_VOLUME, HLS_VOLUME} & set(volume_definitions(DEV_COMPOSE))


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_only_the_api_and_the_proxy_share_a_volume_with_the_media_box(
    environment: Mapping[str, str],
) -> None:
    """The API reaches the box through its socket and the proxy reads what it writes,
    each through a mount that is read-only: connecting to a socket writes nothing to
    the file system it lies in. Nothing else of the stack mounts either volume: not the
    relay and `migrate`, which run the API's image, and not the web app. The socket the
    API is told to connect to (`BROADCAST_MEDIA_SOCKET`) lies in the directory both
    mount, and the proxy serves from the directory the box writes into."""
    mounting = {name: volumes_of(service) for name, service in STACK.items()}
    of_the_socket = {name: v[SOCKET_VOLUME] for name, v in mounting.items() if SOCKET_VOLUME in v}
    of_the_video = {name: v[HLS_VOLUME] for name, v in mounting.items() if HLS_VOLUME in v}
    assert of_the_socket == {"media": (SOCKET_DIRECTORY, False), "api": (SOCKET_DIRECTORY, True)}
    assert of_the_video == {"media": (HLS_DIRECTORY, False), "nginx": (HLS_DIRECTORY, True)}
    assert set(mounting["media"]) == {SOCKET_VOLUME, HLS_VOLUME}
    assert "volumes" not in anchor_of(COMPOSE, "backend")
    for name, service in OVERLAY.items():
        assert not {SOCKET_VOLUME, HLS_VOLUME} & set(volumes_of(service)), name

    socket = PurePosixPath(environment["BROADCAST_MEDIA_SOCKET"])
    assert socket.is_absolute()
    assert socket.parent == SOCKET_DIRECTORY
    [(served,)] = HLS.inner("alias")
    assert served.startswith(f"{HLS_DIRECTORY}/")


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_media_box_has_room_for_the_live_streams_it_may_run(
    environment: Mapping[str, str],
) -> None:
    """One transcode is given 256 MiB and one CPU. What the box writes into its volume
    is memory of its own container, so its limit holds that volume as well. A transcode
    with two threads peaked at 28 processes and threads on the VM; the box may have at
    least 32 for every live stream.

    A live stream keeps the segments its playlist names, three more (the two the box
    keeps after they left the playlist and the one it is writing), each of the bit rate
    people watch for the seconds of a segment, with 15% for the transport stream around
    it. The volume has four times the room of that for every live stream that may run.
    """
    live = int(environment["BROADCAST_MAX_LIVE"])
    volume = docker_bytes(environment["MEDIA_HLS_TMPFS_SIZE"])
    memory = docker_bytes(environment["MEDIA_MEMORY_LIMIT"])
    cpus = float(environment["MEDIA_CPUS"])

    needed = 256 * MIB * live + volume
    assert memory >= needed, (
        f"MEDIA_MEMORY_LIMIT={environment['MEDIA_MEMORY_LIMIT']} is below {needed // MIB} MiB: "
        "256 MiB x BROADCAST_MAX_LIVE + MEDIA_HLS_TMPFS_SIZE"
    )
    assert cpus >= live, "MEDIA_CPUS is below BROADCAST_MAX_LIVE"
    assert 1 <= int(environment["MEDIA_FFMPEG_THREADS"]) <= math.ceil(cpus), (
        "MEDIA_FFMPEG_THREADS is above MEDIA_CPUS rounded up"
    )
    assert int(environment["MEDIA_PIDS_LIMIT"]) >= 32 * live

    kept = int(environment["BROADCAST_HLS_PLAYLIST_SEGMENTS"]) + 3
    kbps = int(environment["BROADCAST_VIDEO_KBPS"]) + int(environment["BROADCAST_AUDIO_KBPS"])
    segment = kbps * 125 * int(environment["BROADCAST_HLS_SEGMENT_SECONDS"]) * 1.15
    held = math.ceil(4 * live * kept * segment)
    assert volume >= held, (
        f"MEDIA_HLS_TMPFS_SIZE={environment['MEDIA_HLS_TMPFS_SIZE']} is below {held} bytes: "
        f"four times {live} x {kept} segments of {math.ceil(segment)} bytes"
    )


VM_MEMORY: Final[int] = 7782 * MIB
"""The memory of the VM, which has no swap: 7.6 GiB (docs/SERVER_INFRASTRUCTURE.md §5)."""


def test_the_memory_limits_of_the_deployed_stack_fit_the_vm() -> None:
    """A limit is a ceiling and no reservation, but if every container were at its own
    at once, `migrate` included, the VM still holds them: nothing is promised twice.
    docs/SERVER_INFRASTRUCTURE.md §12 states the sum; a service that is added and a
    limit that is raised change it there."""
    limits: dict[str, int] = {}
    for name, service in STACK.items():
        [(variable, _)] = REFERENCE.findall(scalar(service, "mem_limit"))
        limits[name] = docker_bytes(DEPLOYED[variable])
    together = sum(limits.values())
    assert together <= VM_MEMORY, (
        f"the limits of the deployed stack add up to {together // MIB} MiB, "
        f"above the {VM_MEMORY // MIB} MiB of the VM: "
        + ", ".join(f"{name} {limit // MIB}" for name, limit in limits.items())
    )


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_proxy_is_handed_the_figures_of_live_video_the_api_goes_by(
    environment: Mapping[str, str],
) -> None:
    """The largest piece a broadcaster sends and the seconds a yes is remembered are
    settings of the API. Compose hands the proxy the same two values under names of its
    own, which are no keys of `.env`: one figure cannot be set to two values. The piece
    limit is written in bytes, which nginx reads as a size, and lies above the limit of
    every other body; a yes is remembered for 1 to 60 seconds, never for none. A caller
    has at least one piece on its way at a time. The proxy relays a piece without
    holding it, so its memory does not grow with the size of an upload: 64 MiB carry
    it."""
    handed = handed_to_nginx(STACK["nginx"], environment)
    assert handed["NGINX_BROADCAST_CHUNK_MAX_BYTES"] == environment["BROADCAST_CHUNK_MAX_BYTES"]
    assert (
        handed["NGINX_BROADCAST_ACCESS_CACHE_SECONDS"]
        == (environment["BROADCAST_ACCESS_CACHE_SECONDS"])
    )
    for name in ("NGINX_BROADCAST_CHUNK_MAX_BYTES", "NGINX_BROADCAST_ACCESS_CACHE_SECONDS"):
        assert name not in EXAMPLE, name
        assert name not in INFRA_ONLY_VARIABLES, name
    assert "NGINX_BROADCAST_UPLOADS_PER_CALLER" in INFRA_ONLY_VARIABLES

    assert re.fullmatch(r"[1-9][0-9]*", handed["NGINX_BROADCAST_CHUNK_MAX_BYTES"])
    assert int(handed["NGINX_BROADCAST_CHUNK_MAX_BYTES"]) > docker_bytes(
        handed["NGINX_CLIENT_MAX_BODY_SIZE"]
    )
    assert 1 <= int(handed["NGINX_BROADCAST_ACCESS_CACHE_SECONDS"]) <= 60
    assert int(handed["NGINX_BROADCAST_UPLOADS_PER_CALLER"]) >= 1
    assert docker_bytes(environment["NGINX_MEMORY_LIMIT"]) >= 64 * MIB


# --- The commands of the Makefile ---------------------------------------------------------


def test_the_overlay_is_merged_only_where_a_command_names_it() -> None:
    """Compose merges a file called `compose.override.yaml` over `compose.yaml` without
    being told to. The tree has none, so a plain `docker compose` is the deployed stack;
    the Makefile names the overlay for this machine and `compose.yaml` alone for the VM."""
    for name in ("compose.override.yaml", "compose.override.yml", "docker-compose.override.yml"):
        assert not (REPO_ROOT / name).exists(), name
    local = re.search(r"^COMPOSE := (.*)$", MAKEFILE, re.MULTILINE)
    assert local is not None
    assert local.group(1) == "docker compose -f compose.yaml -f compose.dev.yaml"
    on_vm = re.search(r"^VM_COMPOSE = (.*)$", MAKEFILE, re.MULTILINE)
    assert on_vm is not None
    assert on_vm.group(1).endswith("docker compose -f compose.yaml")


def make_target(name: str) -> tuple[list[str], str, list[str]]:
    """What a target of the Makefile needs, what `make` says about it and its recipe."""
    found = re.search(
        rf"^{re.escape(name)}:(?P<needs>[^#\n]*)(?:## (?P<says>.*))?\n(?P<recipe>(?:\t.*\n)*)",
        MAKEFILE,
        re.MULTILINE,
    )
    assert found is not None, name
    recipe = [line.removeprefix("\t") for line in found["recipe"].splitlines()]
    return found["needs"].split(), found["says"] or "", recipe


def test_make_e2e_runs_the_browser_suite_against_the_stack_that_is_up() -> None:
    """The target runs the script `e2e` of the frontend and nothing else: it needs no
    other target, so it starts, builds and seeds no stack, and where the stack is and
    how a person is named to it is the runner's to read from its environment. It is no
    part of `make test`, which needs no stack. `make` alone lists it, which takes a
    pattern that knows a digit in a name."""
    needs, says, recipe = make_target("e2e")
    assert needs == []
    assert recipe == ["$(FRONTEND) npm run e2e"]
    assert "e2e" in json.loads(FRONTEND_PACKAGE)["scripts"], "frontend/package.json"

    phony = re.search(r"^\.PHONY:((?:.*\\\n)*.*)$", MAKEFILE, re.MULTILINE)
    assert phony is not None
    assert "e2e" in phony.group(1).replace("\\", " ").split()

    assert says
    listed_by_make = re.search(r"/\^(\[[^\]]+\]\+):\.\*## /", MAKEFILE)
    assert listed_by_make is not None
    assert re.fullmatch(listed_by_make.group(1), "e2e")

    tests, _, _ = make_target("test")
    assert tests == ["test-backend", "test-frontend"]
    for part in tests:
        assert "e2e" not in " ".join(make_target(part)[2]), part


def test_make_test_media_runs_the_image_test_of_the_media_box() -> None:
    """The target runs the tests marked `media` and nothing else: they build the image
    of the box and run it as `compose.yaml` does, under names of their own, so it needs
    no other target and no stack. A plain `pytest` leaves the marker out, and with it
    `make test`. `make` alone lists the target."""
    needs, says, recipe = make_target("test-media")
    assert needs == []
    assert recipe == ["$(BACKEND) uv run pytest -m media"]
    assert says

    phony = re.search(r"^\.PHONY:((?:.*\\\n)*.*)$", MAKEFILE, re.MULTILINE)
    assert phony is not None
    assert "test-media" in phony.group(1).replace("\\", " ").split()

    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert "-m 'not scylla and not media'" in pyproject
    tests, _, _ = make_target("test")
    for part in tests:
        assert "media" not in " ".join(make_target(part)[2]), part


def test_the_vm_deploys_itself_without_a_connection_and_without_a_secret() -> None:
    """SPEC A.10: the team's runner is the VM, and the Deploy workflow runs
    `make deploy DEPLOY_LOCAL=1` on it. With that variable a command for the VM runs in
    a shell of the machine make is on, started in the home directory as an SSH session
    is, and the tree is mirrored into a directory of that machine; without it both go
    over SSH. A machine answers its own address whatever its firewall says, so the
    local form of `deploy-check` leaves the probe from outside alone, and the workflow
    runs it in a second job, on a machine that is not the VM. The workflow reads no
    secret: the VM's own file is on the disk the first job runs on."""
    on_vm = re.search(r"^ON_VM = (.*)$", MAKEFILE, re.MULTILINE)
    assert on_vm is not None
    assert on_vm.group(1) == (
        '$(if $(DEPLOY_LOCAL),env --chdir="$$HOME" sh -c,ssh -o BatchMode=yes $(DEPLOY_SSH_HOST))'
    )
    mirror = re.search(r"^MIRROR = (.*)$", MAKEFILE, re.MULTILINE)
    assert mirror is not None
    assert mirror.group(1) == (
        '$(if $(DEPLOY_LOCAL),"$$HOME/$(DEPLOY_PATH)/",'
        "--rsh='ssh -o BatchMode=yes' '$(DEPLOY_SSH_HOST):$(DEPLOY_PATH)/')"
    )
    assert make_target("deploy-sync")[2][1].endswith(" ./ $(MIRROR)")
    for line in MAKEFILE.splitlines():
        if line.startswith("\t"):
            assert "ssh " not in line, line

    probe = "\n".join(make_target("deploy-check")[2])
    local, _, remote = probe.partition("else")
    assert 'if [ -n "$(DEPLOY_LOCAL)" ]; then' in local
    assert "check.sh outside" not in local
    assert "check.sh outside $$endpoint" in remote

    jobs = re.split(
        r"^  (?=[a-z-]+:\n)", DEPLOY_WORKFLOW.partition("\njobs:\n")[2], flags=re.MULTILINE
    )
    by_name = {job.partition(":")[0]: job for job in jobs if job.strip()}
    assert list(by_name) == ["deploy", "outside"]
    assert "runs-on: self-hosted" in by_name["deploy"]
    assert "run: make deploy DEPLOY_LOCAL=1" in by_name["deploy"]
    assert "needs: deploy" in by_name["outside"]
    assert "runs-on: self-hosted" not in by_name["outside"]
    assert "sh infra/deploy/check.sh outside $ENDPOINT" in by_name["outside"]
    assert "secrets." not in DEPLOY_WORKFLOW
    triggers = DEPLOY_WORKFLOW.partition("\non:\n")[2].partition("\n\n")[0]
    assert triggers.strip() == "workflow_dispatch:"


# --- What the deployment check asks of the running stack -------------------------------------

CHECK_HEADING: Final[re.Pattern[str]] = re.compile(r"^# (\d+)\. .*-{3,}$", re.MULTILINE)
"""The line a numbered check of `infra/deploy/check.sh` begins with."""


def checks_of(script: str) -> dict[int, str]:
    """Number to text of the checks of `check.sh`, each from its heading to the next."""
    pieces = CHECK_HEADING.split(script)
    return {int(number): body for number, body in zip(pieces[1::2], pieces[2::2], strict=True)}


def test_the_deployment_check_reads_the_open_files_and_counts_the_processes_of_the_api() -> None:
    """Check 14 of `infra/deploy/check.sh`: the running nginx and api have the limits of
    open files `.env` names, the two variables `compose.yaml` sets them from, and one
    process serves the API. It reads each limit from the process it holds for and
    counts the processes of the api container that hold a listening socket.

    It is the last check and runs for every tree: the checks of a production tree
    before it end the script nowhere, whatever `APP_ENV` is. The header of the script
    lists it."""
    checks = checks_of(CHECK)
    assert list(checks) == sorted(checks)
    number, check = list(checks.items())[-1]
    assert number == 14
    assert re.search(r"^# {1,2}14\. ", CHECK, re.MULTILINE), "the list at the top of the script"

    for service, variable in NOFILE_LIMITS.items():
        assert f"$(setting {variable})" in check, variable
        assert f"exec -T {service} " in check, service
    assert "exec -T nginx cat /proc/1/limits" in check
    assert "exec -T api python -c" in check
    assert '[ "$serving" = "1" ]' in check
    assert "/proc/net/tcp" in check

    assert code_of(CHECK).rstrip().endswith('echo "All checks passed."')
    production_only = checks[10]
    assert 'if [ "$APP_ENV" = "production" ]; then' in production_only
    assert "exit" not in code_of(production_only)


def test_the_deployment_check_asks_the_running_stack_about_live_video() -> None:
    """Checks 11 to 13 of `infra/deploy/check.sh`, between the checks of a production
    tree and the last one, each listed at the top of the script.

    11 holds the running media box against `compose.yaml`: healthy, no network, a
    read-only root, no variable of a store or of an admin, and reached by the API. 12
    asks for a playlist as nobody, as a caller the API refuses, as another site and
    from the web app's container. 13 sends the body the proxy refuses everywhere else
    as a piece of a live stream, and one above the limit of a piece."""
    checks = checks_of(CHECK)
    assert list(checks) == list(range(1, 15))
    for number in (11, 12, 13):
        assert re.search(rf"^# {{1,2}}{number}\. ", CHECK, re.MULTILINE), number

    box = checks[11]
    assert "ps --quiet media" in box
    assert "{{ .State.Health.Status }}" in box
    assert "{{ .HostConfig.NetworkMode }} {{ .HostConfig.ReadonlyRootfs }}" in box
    assert '[ "$isolation" = "none true" ]' in box
    assert "(POSTGRES|REDIS|SCYLLA)" in box
    assert "ADMIN_EMAILS" in box
    assert """jq -r '.media?'""" in box

    video = checks[12]
    playlist = re.search(r"^PLAYLIST=(\S+)$", video, re.MULTILINE)
    assert playlist is not None
    assert HLS is regex_location(playlist.group(1))
    assert "expect 401 UNAUTHENTICATED" in video
    assert video.count("expect 403 FORBIDDEN") == 3
    assert "Sec-Fetch-Site: cross-site" in video
    assert 'from_the_web_app "$PLAYLIST"' in video

    piece = checks[13]
    address = re.search(r'^PIECES="\$BASE(/[^?"]+)\?', piece, re.MULTILINE)
    assert address is not None
    assert CHUNKS is regex_location(address.group(1))
    assert "$(setting BROADCAST_CHUNK_MAX_BYTES)" in piece
    assert "/api/me/profile" in piece
    assert '[ "$STATUS" = "413" ]' in piece
    assert '[ "$STATUS" != "413" ]' in piece
    assert "expect 413 VALIDATION_ERROR" in piece


# --- How the proxy configuration gets into the container ------------------------------


def test_the_proxy_configuration_is_mounted_by_directory() -> None:
    """A mounted file stays the file it was when the container was created. A mounted
    directory shows a file that was replaced since (`make deploy` replaces them) to the
    next start of the container."""
    deployed = mounts_of(STACK["nginx"])
    development = mounts_of(OVERLAY["nginx"])
    assert list(deployed) == ["./infra/nginx/conf.d"]
    assert list(development) == ["./infra/nginx/dev"]
    for source in (*deployed, *development):
        assert (REPO_ROOT / source).is_dir(), source


def test_the_directory_the_deployed_stack_mounts_holds_the_production_file_alone() -> None:
    """Whatever lies beside that file is rendered and loaded with it."""
    files = sorted(path.name for path in PROXY_DIRECTORY.iterdir() if path.name != ".DS_Store")
    assert files == ["default.conf.template"]


def test_each_mounted_directory_is_rendered_to_where_nginx_reads_it() -> None:
    """The image renders `<templates>/<path>.template` to `<output>/<path>`. The
    production directory has to land in the directory the image's own nginx.conf loads,
    the development one in the directory the server block of the proxy includes."""
    output = PurePosixPath(scalar(STACK["nginx"], "  NGINX_ENVSUBST_OUTPUT_DIR"))
    assert scalar(STACK["nginx"], "  NGINX_ENVSUBST_FILTER") == "^NGINX_"

    [production] = mounts_of(STACK["nginx"]).values()
    assert output / production.relative_to(TEMPLATES) == LOADED_BY_THE_IMAGE

    [development] = mounts_of(OVERLAY["nginx"]).values()
    included = output / development.relative_to(TEMPLATES) / "*.conf"
    assert SERVER.inner("include") == [(str(included),)]
    assert named(IDENTITY_TREE, "include") == []
    assert not included.is_relative_to(LOADED_BY_THE_IMAGE)


def test_every_placeholder_of_a_template_is_passed_by_the_compose_file_that_mounts_it() -> None:
    """A placeholder nobody passes stays in the file, and nginx does not start. A
    variable that is passed and that no template uses is a tunable without effect."""
    steering = {"NGINX_ENVSUBST_OUTPUT_DIR", "NGINX_ENVSUBST_FILTER"}
    assert placeholders(PROXY) == passed_to_nginx(STACK["nginx"]) - steering
    assert placeholders(IDENTITY) == passed_to_nginx(OVERLAY["nginx"])
    assert placeholders(SWITCHER) == set()


# --- Who is calling ---------------------------------------------------------------------


def test_the_identity_headers_reach_the_api_exactly_as_they_arrived() -> None:
    """Behind the portal the two headers are the sign-in. On the way to the API the
    proxy neither sets, changes nor removes them: a second line of the same name arrives
    as a second line, and a request without them is refused there. That holds for the
    question the proxy itself asks about a viewer of a live stream as well: the API
    judges that caller by the headers the viewer's request came with."""
    assert len(API_LOCATIONS) == 3
    to_the_api = [
        (location, headers)
        for location, headers in proxied_locations(SERVER)
        if upstream_of(location) == "$api_upstream"
    ]
    assert [location for location, _ in to_the_api] == [*API_LOCATIONS, ACCESS], (
        "what a browser asks, and what the proxy itself asks before it serves video"
    )
    for location, headers in to_the_api:
        assert location.inner("proxy_set_header") == [], location.arguments
        assert headers == SERVER.inner("proxy_set_header")
        assert not IDENTITY_HEADERS & set(sent_upstream(headers)), location.arguments
    assert named(PROXY_TREE, "proxy_pass_request_headers") == []


def test_the_web_app_is_told_nothing_about_the_caller() -> None:
    """It serves the same pages to everybody. Every location that passes a request to it
    takes both identity headers out, and no location passes to anything but the API and
    the web app."""
    to_the_web = [
        (location, headers)
        for location, headers in proxied_locations(SERVER)
        if upstream_of(location) == "$web_upstream"
    ]
    to_the_api = [*API_LOCATIONS, ACCESS]
    assert len(to_the_web) + len(to_the_api) == len(named(PROXY_TREE, "proxy_pass"))
    assert {location.arguments for location, _ in to_the_web} == {("/",), ("/_next/",)}
    for location, headers in to_the_web:
        sent = sent_upstream(headers)
        for header in IDENTITY_HEADERS:
            assert sent[header] == ("",), location.arguments


def test_the_production_proxy_names_the_identity_headers_only_to_take_them_out() -> None:
    """The two lines of the web app's location are the only directives of the file that
    write either header's name: nothing sets one, and nothing else removes one."""
    naming = [
        (directive.name, directive.arguments)
        for directive in all_of(PROXY_TREE)
        if any(
            header in argument.lower()
            for argument in directive.arguments
            for header in IDENTITY_HEADERS
        )
    ]
    assert sorted(naming) == sorted(
        [("proxy_set_header", (ID_HEADER, "")), ("proxy_set_header", (NAME_HEADER, ""))]
    )


def test_the_production_proxy_file_has_no_trace_of_the_development_identity() -> None:
    """`infra/deploy/check.sh` looks for the same words in the configuration of the
    running proxy, comments included."""
    deployed = PROXY.lower()
    for word in ("dev_user", "__dev", "cookie_dev"):
        assert word not in deployed, word


def test_the_development_proxy_always_sets_both_identity_headers() -> None:
    """At the level of the server block, outside every condition and location: whatever
    a request says about itself in these two headers is replaced."""
    at_server_level = [
        d.arguments[0].lower() for d in IDENTITY_TREE if d.name == "proxy_set_header"
    ]
    assert sorted(at_server_level) == sorted([ID_HEADER.lower(), NAME_HEADER.lower()])
    assert len(named(IDENTITY_TREE, "proxy_set_header")) == len(at_server_level)


def test_the_development_caller_is_named_to_the_api_alone() -> None:
    """The development file is included at the level of the server block, so its two
    lines join the request headers of the server, which are the API's. The location of
    the web app has a list of its own and takes both headers out there as well."""
    named_by_the_file = sent_upstream(
        [d.arguments for d in IDENTITY_TREE if d.name == "proxy_set_header"]
    )
    with_the_file = Directive(SERVER.name, SERVER.arguments, (*SERVER.block, *IDENTITY_TREE))
    for location, headers in proxied_locations(with_the_file):
        sent = sent_upstream(headers)
        for header in IDENTITY_HEADERS:
            if upstream_of(location) == "$api_upstream":
                assert sent[header] == named_by_the_file[header], location.arguments
            else:
                assert sent[header] == ("",), location.arguments


def test_the_development_caller_comes_from_two_cookies_or_from_the_env() -> None:
    """The cookies `dev_user_id` and `dev_user_name`, else DEV_USER_ID and DEV_USER_NAME,
    and never what the request's own identity headers say."""
    sent = {
        d.arguments[0].lower(): d.arguments[1] for d in named(IDENTITY_TREE, "proxy_set_header")
    }
    assigned_to: dict[str, set[str]] = {}
    for assignment in named(IDENTITY_TREE, "set"):
        variable, value = assignment.arguments
        assigned_to.setdefault(variable, set()).add(value)
    assert assigned_to[sent[ID_HEADER.lower()]] == {"$cookie_dev_user_id", "${NGINX_DEV_USER_ID}"}
    assert assigned_to[sent[NAME_HEADER.lower()]] == {
        "$cookie_dev_user_name",
        "${NGINX_DEV_USER_NAME}",
    }
    assert scalar(OVERLAY["nginx"], "  NGINX_DEV_USER_ID") == "${DEV_USER_ID:?}"
    assert scalar(OVERLAY["nginx"], "  NGINX_DEV_USER_NAME") == "${DEV_USER_NAME:?}"
    for directive in all_of(IDENTITY_TREE):
        for argument in directive.arguments:
            assert request_variable(ID_HEADER) not in argument.lower(), directive
            assert request_variable(NAME_HEADER) not in argument.lower(), directive


def test_the_switcher_is_served_from_the_mounted_directory() -> None:
    [(served,)] = location_of(IDENTITY_TREE, "=", "/__dev/identity").inner("alias")
    [mounted] = mounts_of(OVERLAY["nginx"]).values()
    assert PurePosixPath(served).parent == mounted
    assert (DEVELOPMENT_DIRECTORY / PurePosixPath(served).name).is_file()


def test_the_switcher_stores_exactly_the_two_cookies_the_proxy_reads() -> None:
    """The page stores and clears them through its one `store(...)` function."""
    read = set(re.findall(r"\$cookie_(\w+)", IDENTITY))
    stored = re.findall(r'\bstore\("(\w+)"', SWITCHER)
    assert read == {"dev_user_id", "dev_user_name"}
    assert set(stored) == read
    assert all(stored.count(cookie) == 2 for cookie in read), stored
    assert SWITCHER.count("document.cookie") == 1


def test_the_switcher_calls_the_admins_of_the_example_env_admins_and_nobody_else() -> None:
    """`ADMIN_EMAILS` names the bootstrap admins, and it is the one role `.env` knows.
    A role given in the admin panel is kept with the account, where this page cannot see
    it. Each person offered has one label; the caller of `DEV_USER_ID` is among them."""
    offered = dict(re.findall(r'data-id="([^"]+)".*?<em>([^<]*)</em>', SWITCHER, re.DOTALL))
    assert len(offered) == len(re.findall(r"data-id=", SWITCHER))
    assert listed("ADMIN_EMAILS")
    assert EXAMPLE["DEV_USER_ID"] in offered
    called_admin = {address for address, label in offered.items() if label == "admin"}
    assert called_admin == set(listed("ADMIN_EMAILS"))


def test_the_switcher_loads_nothing_from_elsewhere() -> None:
    """One page of plain HTML: no framework, no font, no script from another origin."""
    assert "://" not in SWITCHER
    assert re.search(r"<(?:link|img|iframe)\b|<script[^>]*\bsrc=", SWITCHER) is None


# --- What the proxy itself guarantees ---------------------------------------------------


def test_the_one_location_with_request_headers_of_its_own_repeats_the_servers() -> None:
    """nginx gives a location the `proxy_set_header` lines of the level around it only
    while the location has none of its own: one line in a location drops all of them
    there. The location of the web app needs two lines of its own, so it repeats the
    list of the server. No other location of either file sets a request header."""
    with_their_own = [
        location for location in named(PROXY_TREE, "location") if location.inner("proxy_set_header")
    ]
    [web] = with_their_own
    assert web.arguments == ("/",)
    assert web.inner("proxy_set_header") == [
        *SERVER.inner("proxy_set_header"),
        (ID_HEADER, ""),
        (NAME_HEADER, ""),
    ]
    for location in named(IDENTITY_TREE, "location"):
        inside = [d for d in location.walk() if d.name == "proxy_set_header"]
        assert inside == [], location.arguments


def test_a_location_that_adds_a_response_header_keeps_the_security_headers() -> None:
    assert SERVER.inner("add_header_inherit") == [("merge",)]
    assert len(named(PROXY_TREE, "add_header_inherit")) == 1
    assert named(IDENTITY_TREE, "add_header_inherit") == []
    always = {arguments[0]: arguments[1:] for arguments in SERVER.inner("add_header")}
    assert always["Content-Security-Policy"] == ("${NGINX_CONTENT_SECURITY_POLICY}", "always")
    assert always["X-Content-Type-Options"] == ("nosniff", "always")
    assert {"Referrer-Policy", "Strict-Transport-Security"} <= set(always)


def test_a_browser_is_told_to_name_the_origin_alone_in_a_referer() -> None:
    """An address of the app can hold a code to redeem (`/wallet?code=...`). Under
    `strict-origin` the Referer of every request a page makes is the origin without path
    and query, to this app as to any other site, so the code travels in none. The
    policy is the server's, on every answer, and no location sets another."""
    policies = [
        directive.arguments
        for directive in (*all_of(PROXY_TREE), *all_of(IDENTITY_TREE))
        if directive.name == "add_header" and directive.arguments[0].lower() == "referrer-policy"
    ]
    assert policies == [("Referrer-Policy", "strict-origin", "always")]
    assert policies[0] in SERVER.inner("add_header")


def test_the_body_limit_comes_from_the_env_and_one_location_has_its_own() -> None:
    """The limit of the server holds for every location but one: the pieces of a live
    stream, which may be as large as the API takes them."""
    assert SERVER.inner("client_max_body_size") == [("${NGINX_CLIENT_MAX_BODY_SIZE}",)]
    with_their_own = [
        location
        for location in named(PROXY_TREE, "location")
        if location.inner("client_max_body_size")
    ]
    assert with_their_own == [CHUNKS]
    assert CHUNKS.inner("client_max_body_size") == [("${NGINX_BROADCAST_CHUNK_MAX_BYTES}",)]
    assert len(named(PROXY_TREE, "client_max_body_size")) == 2
    assert named(IDENTITY_TREE, "client_max_body_size") == []


def test_the_visitors_address_is_the_last_entry_of_the_portals_header() -> None:
    """Whatever stands before the last entry was supplied by the client: no recursion."""
    assert [d.arguments for d in named(PROXY_TREE, "real_ip_header")] == [
        ("X-Original-Forwarded-For",)
    ]
    assert [d.arguments for d in named(PROXY_TREE, "real_ip_recursive")] == [("off",)]


def test_address_and_scheme_are_believed_from_the_same_private_peers_only() -> None:
    for_the_address = {d.arguments[0] for d in PROXY_TREE if d.name == "set_real_ip_from"}
    peers = geo_of(PROXY_TREE, "$peer_is_trusted")
    peer_address, trusted = peers.arguments
    for_the_scheme = {entry.name for entry in peers.block if entry.arguments == ("1",)}
    assert peer_address == "$realip_remote_addr"
    assert peers.inner("default") == [("0",)]
    assert for_the_address
    assert for_the_address == for_the_scheme
    for network in for_the_address:
        assert ipaddress.ip_network(network).is_private, network

    scheme = maps_to("$client_scheme")
    assert scheme.arguments[0] == trusted
    assert scheme.inner("default") == [("$scheme",)]
    [(forwarded,)] = scheme.inner("1")
    assert maps_to(forwarded).arguments[0] == "$http_x_forwarded_scheme"
    [(upstream_scheme,)] = [
        arguments[1:]
        for arguments in SERVER.inner("proxy_set_header")
        if arguments[0] == "X-Forwarded-Proto"
    ]
    assert upstream_scheme == "$client_scheme"


CONNECTION_ZONES: Final[dict[str, str]] = {
    zone.arguments[1].removeprefix("zone=").split(":")[0]: zone.arguments[0]
    for zone in named(PROXY_TREE, "limit_conn_zone")
}
"""Name to key of the zones in which the proxy counts open connections."""


def test_the_outer_limits_count_per_caller_as_the_proxy_names_them() -> None:
    """Visitors behind one address must not share a bucket, so every limit is keyed on
    the caller: `$api_caller`, with the address for a request that names nobody. No map
    of the proxy reads the identity header itself, so nothing is counted by what a
    request says where the proxy names the caller. Every location a browser reaches
    under /api/ counts a request, the one that serves video included."""
    [callers] = [
        directive
        for directive in PROXY_TREE
        if directive.name == "map" and directive.arguments[0] == API_CALLER
    ]
    caller = callers.arguments[1]
    assert callers.inner("default") == [("$binary_remote_addr",)]
    [named_caller] = [rule for rule in callers.block if rule.name != "default"]
    assert named_caller.name.startswith("~")
    assert named_caller.arguments == (API_CALLER,)
    assert [
        directive
        for directive in PROXY_TREE
        if directive.name == "map" and directive.arguments[0] == request_variable(ID_HEADER)
    ] == []
    [requests] = named(PROXY_TREE, "limit_req_zone")
    assert requests.arguments[0] == caller
    assert requests.arguments[2] == "rate=${NGINX_RATE_LIMIT}"
    assert set(CONNECTION_ZONES) == {"streams_per_caller", "uploads_per_caller"}
    for name, key in CONNECTION_ZONES.items():
        assert key == caller, name
    assert len(UNDER_API) == 4
    for location in UNDER_API:
        [(zone, burst, _)] = location.inner("limit_req")
        assert zone == requests.arguments[1].split(":")[0], location.arguments
        assert burst == "burst=${NGINX_RATE_LIMIT_BURST}", location.arguments


def test_each_file_of_the_proxy_says_once_who_the_caller_of_the_limits_is() -> None:
    """The server block sets `$api_caller` to the id the portal sent, in a line of its
    own level before the one that includes the development file. That file ends with
    the line that sets it again, to the variable it sends the API as the caller's id.
    nginx runs the `set` lines of a server block in their order, so on a laptop the
    last one holds, and in the deployed stack, which includes no file, the first."""
    assigning = [
        position
        for position, directive in enumerate(SERVER.block)
        if directive.name == "set" and directive.arguments[0] == API_CALLER
    ]
    [position] = assigning
    assert SERVER.block[position].arguments == (API_CALLER, request_variable(ID_HEADER))
    [including] = [
        position for position, directive in enumerate(SERVER.block) if directive.name == "include"
    ]
    assert position < including
    in_the_proxy = [
        directive
        for directive in all_of(PROXY_TREE)
        if directive.name == "set" and directive.arguments[0] == API_CALLER
    ]
    assert len(in_the_proxy) == 1, "none inside a location or a condition"

    last = IDENTITY_TREE[-1]
    [sent_as_the_id] = [
        directive.arguments[1]
        for directive in IDENTITY_TREE
        if directive.name == "proxy_set_header" and directive.arguments[0] == ID_HEADER
    ]
    assert (last.name, last.arguments) == ("set", (API_CALLER, sent_as_the_id))
    in_the_development_file = [
        directive
        for directive in all_of(IDENTITY_TREE)
        if directive.name == "set" and directive.arguments[0] == API_CALLER
    ]
    assert in_the_development_file == [last]


CLIENT_ADDRESS: Final[str] = "<the address of the client>"
"""What stands for `$binary_remote_addr` where a test asks for the bucket of a request."""


def bucket_of(request: Mapping[str, str], development: Iterable[Directive] = ()) -> str:
    """The key under which the proxy counts a request against its limits: `$caller`, for
    what the request brought (by nginx variable) and for the file the server block
    includes."""
    known = set_by_the_server(request, development) | {"$binary_remote_addr": CLIENT_ADDRESS}
    return looked_up("$caller", {}, known)


def test_the_deployed_proxy_counts_a_request_under_the_id_the_portal_sent() -> None:
    """One bucket per id, and the address for a request that names nobody or whose id
    is longer than the 255 bytes a connection limit accepts as a key. A cookie of the
    development stand-in names nobody here."""
    header = request_variable(ID_HEADER)
    assert bucket_of({header: "ab12cd@ethz.ch"}) == "ab12cd@ethz.ch"
    assert bucket_of({header: "ef34gh@ethz.ch"}) == "ef34gh@ethz.ch"
    assert bucket_of({header: "a" * 255}) == "a" * 255
    assert bucket_of({header: "a" * 256}) == CLIENT_ADDRESS
    assert bucket_of({}) == CLIENT_ADDRESS
    assert bucket_of({"$cookie_dev_user_id": "ab12cd@ethz.ch"}) == CLIENT_ADDRESS


def test_the_development_proxy_counts_a_request_under_the_caller_it_names() -> None:
    """On a laptop a browser sends no identity header: the proxy names the caller from
    a cookie, or from `DEV_USER_ID`. Counted by the header, every window of every
    person would share the one bucket of the laptop's address. Counted by the caller,
    two people have a bucket each, and it is the id the API is told. An identity header
    a request brings picks no bucket, as it names nobody to the API."""
    development = parse(rendered(IDENTITY, handed_to_nginx(OVERLAY["nginx"], EXAMPLE)))
    header = request_variable(ID_HEADER)
    cookie = "$cookie_dev_user_id"
    assert bucket_of({cookie: "anna@ethz.ch"}, development) == "anna@ethz.ch"
    assert bucket_of({cookie: "ben@ethz.ch"}, development) == "ben@ethz.ch"
    assert bucket_of({cookie: "anna@ethz.ch", header: "ben@ethz.ch"}, development) == (
        "anna@ethz.ch"
    )
    assert bucket_of({}, development) == EXAMPLE["DEV_USER_ID"]
    assert bucket_of({header: "ben@ethz.ch"}, development) == EXAMPLE["DEV_USER_ID"]

    [sent_as_the_id] = [
        directive.arguments[1]
        for directive in development
        if directive.name == "proxy_set_header" and directive.arguments[0] == ID_HEADER
    ]
    for request in ({cookie: "anna@ethz.ch"}, {}, {header: "ben@ethz.ch"}):
        variables = set_by_the_server(request, development)
        assert variables[API_CALLER] == variables[sent_as_the_id], request


SITE: Final[str] = "Sec-Fetch-Site"
MODE: Final[str] = "Sec-Fetch-Mode"
DEST: Final[str] = "Sec-Fetch-Dest"


@pytest.mark.parametrize(
    ("site", "mode", "dest", "foreign"),
    [
        # A page of this app, an address typed in, a client that is no browser.
        ("same-origin", "cors", "empty", "0"),
        ("same-origin", "navigate", "document", "0"),
        ("same-origin", "navigate", "iframe", "0"),
        ("none", "navigate", "document", "0"),
        ("", "", "", "0"),
        # What a page of another team's app, or of any other site, makes a browser ask.
        ("same-site", "cors", "empty", "1"),
        ("cross-site", "cors", "empty", "1"),
        ("same-site", "no-cors", "script", "1"),
        ("cross-site", "no-cors", "image", "1"),
        ("cross-site", "websocket", "websocket", "1"),
        ("SAME-SITE", "cors", "empty", "1"),
        ("cross-site", "", "", "1"),
        # The visitor's own window opening an address: a link, the way back from sign-in.
        ("same-site", "navigate", "document", "0"),
        ("cross-site", "navigate", "document", "0"),
        ("cross-site", "navigate", "", "0"),
        # A navigation that is not the window's own.
        ("cross-site", "navigate", "iframe", "1"),
        ("same-site", "navigate", "frame", "1"),
        ("cross-site", "navigate", "object", "1"),
        ("same-site", "navigate", "embed", "1"),
    ],
)
def test_a_request_is_foreign_when_another_site_made_the_browser_send_it(
    site: str, mode: str, dest: str, foreign: str
) -> None:
    """The portal names the visitor whichever page made the browser ask (SPEC A.2).
    `Sec-Fetch-Site` says whose page it was; the window itself may come from anywhere."""
    headers = {name: value for name, value in ((SITE, site), (MODE, mode), (DEST, dest)) if value}

    assert looked_up(FOREIGN_REQUEST, headers) == foreign


FROM_THE_WEB_NETWORK: Final[str] = "$from_the_web_network"
FOREIGN_REQUEST: Final[str] = "$foreign_request"
REFUSED_UNDER_API: Final[tuple[str, ...]] = (FROM_THE_WEB_NETWORK, FOREIGN_REQUEST)
"""The two flags on which the proxy answers 403 under /api/ without asking the API: the
peer is on the network of the web app, or another site made the browser send the
request."""


def test_every_api_location_starts_with_the_two_refusals_of_the_proxy() -> None:
    """Each location a browser reaches under /api/, the event stream and the two of a
    live stream included, begins with one `if` per flag, and each of them does one
    thing: `return 403`. No other `if` stands in the file, so nothing else is decided
    this way."""
    assert len(UNDER_API) == 4
    for location in UNDER_API:
        refusals = location.block[: len(REFUSED_UNDER_API)]
        assert [(refusal.name, refusal.arguments) for refusal in refusals] == [
            ("if", (f"({flag})",)) for flag in REFUSED_UNDER_API
        ], location.arguments
        for refusal in refusals:
            assert [(inside.name, inside.arguments) for inside in refusal.block] == [
                ("return", ("403",))
            ]
    assert len(named(PROXY_TREE, "if")) == len(REFUSED_UNDER_API) * len(UNDER_API)


def test_a_refusal_of_the_proxy_is_answered_before_the_request_is_counted() -> None:
    """Another team's page could otherwise use up the request limit, or the streams, of
    whoever looks at it. `return` belongs to the phase in which nginx rewrites a
    request, which is over before `limit_req` and `limit_conn` see it; the named
    location that answers has no limit of its own and inherits none. Where the proxy
    serves video, the refusal is answered with the sentence of a live stream nobody may
    watch."""
    for location in UNDER_API:
        assert location.inner("limit_req"), location.arguments
        answer = answers_of_nginx(location)[403]
        refused_by = "@broadcast_refused" if location is HLS else "@api_forbidden"
        assert answer.arguments == (refused_by,), location.arguments
        assert named(answer.block, "limit_req") + named(answer.block, "limit_conn") == []
    assert SERVER.inner("limit_req") + SERVER.inner("limit_conn") == []
    assert [d.name for d in PROXY_TREE if d.name in {"limit_req", "limit_conn"}] == []


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_a_peer_on_the_edge_network_other_than_its_gateway_is_from_the_web_network(
    environment: Mapping[str, str],
) -> None:
    """The proxy listens on the network it shares with the web app and hands a request
    under /api/ on with the headers it came with. A request from the web app's container
    never passed the portal, so the proxy refuses it there: every address of `edge`
    raises the flag, except the gateway, from which Docker may hand over what arrives on
    the published port.

    The flag is read from the peer of the connection. `$remote_addr` would be whatever
    the web app's container names in the portal's header, because its address is a
    private one.

    The template is read as the nginx image renders it for this environment.
    """
    tree = parse(rendered(PROXY, handed_to_nginx(STACK["nginx"], environment)))
    peers = geo_of(tree, FROM_THE_WEB_NETWORK)
    assert peers.arguments == ("$realip_remote_addr", FROM_THE_WEB_NETWORK)
    assert {entry.name: entry.arguments for entry in peers.block} == {
        "default": ("0",),
        environment["EDGE_SUBNET"]: ("1",),
        environment["EDGE_GATEWAY"]: ("0",),
    }

    subnet = ipaddress.ip_network(environment["EDGE_SUBNET"])
    gateway = ipaddress.ip_address(environment["EDGE_GATEWAY"])
    for address in subnet:
        assert located(peers, str(address)) == ("0" if address == gateway else "1"), address
    for elsewhere in (
        subnet.network_address - 1,
        subnet.broadcast_address + 1,
        "127.0.0.1",
        "10.0.0.20",
        "172.18.0.1",
        "188.245.14.160",
        "fd00::1",
    ):
        assert located(peers, str(elsewhere)) == "0", elsewhere


@pytest.mark.parametrize(
    ("environment", "with_the_overlay"),
    [(EXAMPLE, False), (DEPLOYED, False), (EXAMPLE, True)],
    ids=["example", "production", "development"],
)
def test_a_rendered_configuration_has_no_placeholder_left_and_no_empty_value(
    environment: Mapping[str, str], with_the_overlay: bool
) -> None:
    """The nginx image replaces the `${NGINX_*}` placeholders it was handed a variable
    for and leaves every other `${...}` as it stands. What a compose file hands over
    has to fill the templates it mounts completely, with a value for each."""
    handed = handed_to_nginx(STACK["nginx"], environment)
    templates = [PROXY]
    if with_the_overlay:
        handed |= handed_to_nginx(OVERLAY["nginx"], environment)
        templates.append(IDENTITY)
    for template in templates:
        for placeholder in placeholders(template):
            assert handed[placeholder] != "", placeholder
        text = rendered(template, handed)
        assert "${" not in code_of(text)
        assert parse(text)


def test_event_streams_are_unbuffered_patient_and_limited_per_caller() -> None:
    """Opening a stream counts against the caller's request limit like any other call,
    and a caller holds a limited number of streams open at once."""
    stream = STREAMS
    assert stream in API_LOCATIONS
    path = re.compile(stream.arguments[1])
    assert path.search("/api/markets/0190f1a2/stream")
    assert not path.search("/api/markets/0190f1a2/history")
    assert not path.search("/markets/0190f1a2/stream")
    zone = "streams_per_caller"
    assert zone in CONNECTION_ZONES
    assert stream.inner("limit_conn") == [(zone, "${NGINX_SSE_STREAMS_PER_CALLER}")]
    counted_there = [
        location
        for location in named(PROXY_TREE, "location")
        if any(arguments[0] == zone for arguments in location.inner("limit_conn"))
    ]
    assert counted_there == [stream], "no other location counts against the streams"
    assert stream.inner("proxy_buffering") == [("off",)]
    assert stream.inner("proxy_read_timeout") == [("${NGINX_SSE_READ_TIMEOUT}",)]
    assert stream.inner("proxy_pass_header") == [("X-Accel-Buffering",)]
    assert stream.inner("limit_req") == location_of(SERVER.block, "/api/").inner("limit_req")


def test_the_access_log_holds_no_query_string_no_referer_and_no_cookie() -> None:
    """A code to redeem arrives as `/wallet?code=...`, a Referer can repeat such an
    address, and the cookies that come along are a visitor's sessions with other
    services of ETH: a browser sends what it holds for `.ethz.ch` to every host below
    it.

    The rule is about the access log, the one log the proxy shapes. A line of nginx's
    error log that is about a request holds the request line as it arrived; the
    template says so, and that it is accepted."""
    [log_format] = named(PROXY_TREE, "log_format")
    name, *parts = log_format.arguments
    written = "".join(parts)
    carriers = (
        r"\$(?:request|request_uri|args|query_string|is_args|arg_\w+"
        r"|http_referer|http_cookie|cookie_\w+|sent_http_set_cookie)(?![a-z_])"
    )
    assert re.findall(carriers, written) == []
    for carrier in ("$request_uri", "$args", "$query_string", "$http_referer", "$http_cookie"):
        assert re.fullmatch(carriers, carrier), carrier
    assert SERVER.inner("access_log") == [("/var/log/nginx/access.log", name)]
    for logging in named(PROXY_TREE, "access_log") + named(IDENTITY_TREE, "access_log"):
        assert logging.arguments in {("off",), ("/var/log/nginx/access.log", name)}

    assert "$request_path" in written
    paths = maps_to("$request_path")
    assert paths.arguments[0] == "$request_uri"
    [rule] = paths.block
    path = re.match(rule.name.removeprefix("~").replace("(?<", "(?P<"), "/wallet?code=ABCD&x=1")
    assert path is not None
    assert path.group(rule.arguments[0].removeprefix("$")) == "/wallet"


def test_the_access_log_says_whether_a_cookie_of_the_portal_arrived_and_no_more() -> None:
    """The portal keeps its session cookie, `_oauth2_proxy`, to itself: a request reaches
    the VM without it. That is what the organizers state (docs/SERVER_INFRASTRUCTURE.md
    §13, check 8), and the log is the standing confirmation: one word per request,
    `true` or `false`, taken from the names of the cookies and never from a value."""
    [log_format] = named(PROXY_TREE, "log_format")
    written = "".join(log_format.arguments[1:])
    field = re.search(r'"portal_cookie":(\$\w+)[,}]', written)
    assert field is not None, "a bare JSON word, not a quoted string"
    flag = maps_to(field.group(1))
    assert flag.arguments[0] == "$http_cookie"
    assert {rule.arguments for rule in flag.block} == {("true",), ("false",)}

    def logged(cookies: str) -> str:
        return looked_up(field.group(1), {"Cookie": cookies} if cookies else {})

    assert logged("_oauth2_proxy=c2VjcmV0") == "true"
    assert logged("theme=dark; _oauth2_proxy_0=c2Vj; _oauth2_proxy_1=cmV0") == "true"
    assert logged("theme=dark;_oauth2_proxy_csrf=c2VjcmV0") == "true"
    assert logged("") == "false"
    assert logged("theme=dark; dev_user_id=admin@ethz.ch") == "false"
    assert logged("theme=_oauth2_proxy") == "false", "a value is not a name"
    assert logged("not_oauth2_proxy=1") == "false", "the name starts with it"


def test_what_nginx_answers_under_api_has_the_error_shape_of_the_api() -> None:
    """403, 429, 502 and 504, and a body above the limit: `{code, message, details}`
    with the words the API itself uses (SPEC A.2). The location that serves video
    answers more statuses and has a test of its own below."""
    under_api = [
        location
        for location in SERVER.block
        if location.name == "location" and "/api/" in location.arguments[-1]
    ]
    assert under_api == UNDER_API
    for location in API_LOCATIONS:
        handler = answers_of_nginx(location)
        assert answer_of(handler[403]) == (
            ForbiddenError.status_code,
            {"code": ErrorCode.FORBIDDEN, "message": ORIGIN_MESSAGE, "details": {}},
        ), "what the API's own Origin check answers"
        assert answer_of(handler[429]) == (
            RateLimitedError.status_code,
            {
                "code": ErrorCode.RATE_LIMITED,
                "message": RateLimitedError.default_message,
                "details": {"retry_after_seconds": 1},
            },
        )
        assert handler[429].inner("add_header") == [("Retry-After", "1", "always")]
        for status in (502, 504):
            assert answer_of(handler[status]) == (
                ServiceUnavailableError.status_code,
                {
                    "code": ErrorCode.SERVICE_UNAVAILABLE,
                    "message": ServiceUnavailableError.default_message,
                    "details": {},
                },
            )
        status, body = answer_of(handler[413])
        assert status == 413
        assert body["code"] == ErrorCode.VALIDATION_ERROR
        assert set(body) == {"code", "message", "details"}


# --- Live video -------------------------------------------------------------------------

API_PATHS: Final[dict[str, dict[str, object]]] = json.loads(
    (REPO_ROOT / "backend" / "openapi.json").read_text(encoding="utf-8")
)["paths"]
"""The operations of the API as its exported contract names them."""

SEGMENT: Final[str] = f"/api/broadcasts/{BROADCAST}/hls/seg-000042.ts"
PLAYLIST: Final[str] = f"/api/broadcasts/{BROADCAST}/hls/index.m3u8"


def test_the_broadcast_of_a_request_for_video_is_read_from_its_address() -> None:
    """One variable says which broadcast a request for video is about. The question to
    the API and the directory the file is served from both take it, so the broadcast
    that is asked about is the broadcast that is served, whatever a location captured.
    It is read from the address as it arrived, and an address that does not begin as a
    playlist's does names none."""
    rules = maps_to("$broadcast_of_request")
    assert rules.arguments[0] == "$request_uri"
    assert rules.inner("default") == [("",)]
    [rule] = [rule for rule in rules.block if rule.name != "default"]
    assert rule.name.startswith("~^"), "a case-sensitive pattern, from the first character on"
    pattern = re.compile(rule.name.removeprefix("~").replace("(?<", "(?P<"))
    captured = rule.arguments[0].removeprefix("$")

    def broadcast_of(address: str) -> str:
        found = pattern.search(address)
        return "" if found is None else found.group(captured)

    assert broadcast_of(PLAYLIST) == BROADCAST
    assert broadcast_of(f"{SEGMENT}?again=1") == BROADCAST
    other = BROADCAST.replace("9", "1")
    assert broadcast_of(f"/api/broadcasts/{other}/hls/../../{BROADCAST}/hls/index.m3u8") == other
    for address in (
        f"/api/broadcasts/{BROADCAST}/chunks",
        f"/api/broadcasts/{BROADCAST.upper()}/hls/index.m3u8",
        f"/api/broadcasts/{BROADCAST[:-1]}/hls/index.m3u8",
        f"/api/broadcasts/{BROADCAST}0/hls/index.m3u8",
        f"/x/api/broadcasts/{BROADCAST}/hls/index.m3u8",
        f"//api/broadcasts/{BROADCAST}/hls/index.m3u8",
        "/api/broadcasts/../hls/index.m3u8",
        "/api/broadcasts/%2e%2e/hls/index.m3u8",
    ):
        assert broadcast_of(address) == "", address

    reading_it = [
        directive.name
        for directive in all_of(PROXY_TREE)
        if any("$broadcast_of_request" in argument for argument in directive.arguments)
    ]
    assert sorted(reading_it) == ["alias", "map", "proxy_cache_key", "proxy_pass"]


def test_a_piece_of_a_live_stream_is_relayed_as_it_arrives_within_limits_of_its_own() -> None:
    """The address of a piece is the API's operation and no other address. The location
    passes the request on like `/api/`, with three things of its own: the body may be
    as large as the API takes a piece, it is handed on while it arrives and never
    collected by the proxy (no body buffer is sized anywhere, so nginx holds its
    default of an upload, whoever sends it), and a caller has a limited number of
    pieces on their way at once. Sending a piece counts against the request limit like
    any other call."""
    kind, pattern = CHUNKS.arguments
    assert kind == "~", "case-sensitive"
    address = re.compile(pattern)
    assert "post" in API_PATHS["/api/broadcasts/{broadcast_id}/chunks"]
    assert address.search(f"/api/broadcasts/{BROADCAST}/chunks")
    for elsewhere in (
        f"/api/broadcasts/{BROADCAST}/chunks/",
        f"/api/broadcasts/{BROADCAST}/chunks/more",
        f"/api/broadcasts/{BROADCAST}/start",
        f"/api/broadcasts/{BROADCAST}/end",
        f"/api/broadcasts/{BROADCAST.upper()}/chunks",
        f"/api/broadcasts/{BROADCAST[:-1]}/chunks",
        f"/x/api/broadcasts/{BROADCAST}/chunks",
        "/api/broadcasts/live",
        "/api/broadcasts/chunks",
        PLAYLIST,
    ):
        assert not address.search(elsewhere), elsewhere

    assert CHUNKS in API_LOCATIONS
    everywhere = location_of(SERVER.block, "/api/")
    its_own = {"limit_conn", "client_max_body_size", "proxy_request_buffering"}
    assert [d for d in CHUNKS.block if d.name not in its_own] == list(everywhere.block)
    assert CHUNKS.inner("limit_conn") == [
        ("uploads_per_caller", "${NGINX_BROADCAST_UPLOADS_PER_CALLER}")
    ]
    assert CHUNKS.inner("client_max_body_size") == [("${NGINX_BROADCAST_CHUNK_MAX_BYTES}",)]
    assert CHUNKS.inner("proxy_request_buffering") == [("off",)]

    unbuffered = [
        location
        for location in named(PROXY_TREE, "location")
        if location.inner("proxy_request_buffering")
    ]
    assert unbuffered == [CHUNKS]
    counted_there = [
        location
        for location in named(PROXY_TREE, "location")
        if any(arguments[0] == "uploads_per_caller" for arguments in location.inner("limit_conn"))
    ]
    assert counted_there == [CHUNKS]
    assert named(PROXY_TREE, "client_body_buffer_size") == []
    assert named(PROXY_TREE, "client_body_in_file_only") == []


def test_a_request_within_the_limits_is_passed_to_the_api_as_it_was() -> None:
    """The location of `/api/` holds what it held before live video: the two refusals,
    the request limit, the four answers of nginx and the upstream, and nothing of a
    live stream. The addresses of live video that are operations of the API fall to it,
    all but the piece."""
    everywhere = location_of(SERVER.block, "/api/")
    assert [directive.name for directive in everywhere.block] == [
        "if",
        "if",
        "limit_req",
        "error_page",
        "error_page",
        "error_page",
        "error_page",
        "proxy_pass",
    ]
    regexes = [
        location.arguments[1].replace("(?<", "(?P<")
        for location in SERVER.block
        if location.name == "location" and location.arguments[0] == "~"
    ]
    assert len(regexes) == 3
    for path, operations in API_PATHS.items():
        if "broadcast" not in path or path.endswith("/chunks"):
            continue
        address = path.replace("{broadcast_id}", BROADCAST).replace("{market_id}", BROADCAST)
        assert not any(re.search(regex, address) for regex in regexes), (path, sorted(operations))


def test_video_is_served_from_the_volume_of_the_media_box_and_only_once_the_api_said_yes() -> None:
    """The address names the playlist or one segment by its number, and nothing else
    of the volume: no other name, no directory, no way up. The proxy serves the file
    itself, to GET alone, after the API answered the question of the next test with a
    yes; the API is no part of serving the bytes. It follows no link out of the volume,
    gives each file its one content type, tells every cache to keep nothing, counts the
    request against the caller's limit, and leaves the access log alone: a hundred
    viewers would otherwise push every other line out of it."""
    kind, pattern = HLS.arguments
    assert kind == "~", "case-sensitive"
    address = re.compile(pattern.replace("(?<", "(?P<"))
    files = f"/api/broadcasts/{BROADCAST}/hls"
    for name in ("index.m3u8", "seg-000000.ts", "seg-000042.ts", "seg-999999.ts"):
        found = address.search(f"{files}/{name}")
        assert found is not None, name
        assert found.group("hls_file") == name
    for elsewhere in (
        f"{files}/",
        f"{files}/seg-42.ts",
        f"{files}/seg-0000042.ts",
        f"{files}/seg-00004a.ts",
        f"{files}/seg-000042.ts.tmp",
        f"{files}/seg-000042.ts/",
        f"{files}/index.m3u8.tmp",
        f"{files}/indexXm3u8",
        f"{files}/other.m3u8",
        f"{files}/sub/index.m3u8",
        f"{files}/../index.m3u8",
        f"{files}/.index.m3u8",
        f"/api/broadcasts/{BROADCAST.upper()}/hls/index.m3u8",
        f"/api/broadcasts/{BROADCAST[:-1]}/hls/index.m3u8",
        f"/x{files}/index.m3u8",
        f"/api/broadcasts/{BROADCAST}/index.m3u8",
        "/api/broadcasts/hls/index.m3u8",
    ):
        assert not address.search(elsewhere), elsewhere
    assert not any("{broadcast_id}/hls" in path for path in API_PATHS), "no operation of the API"

    assert HLS.inner("proxy_pass") == []
    assert HLS.inner("alias") == [(f"{HLS_DIRECTORY}/$broadcast_of_request/$hls_file",)]
    assert [d.arguments for d in named(PROXY_TREE, "alias")] == HLS.inner("alias")
    for word in ("root", "autoindex", "try_files", "index", "dav_methods", "proxy_store"):
        assert named(PROXY_TREE, word) == [], word
    assert HLS.inner("disable_symlinks") == [("on",)]

    [methods] = [directive for directive in HLS.block if directive.name == "limit_except"]
    assert methods.arguments == ("GET",)
    assert [(inside.name, inside.arguments) for inside in methods.block] == [("deny", ("all",))]
    assert HLS.inner("auth_request") == [("/_broadcast_access",)]
    assert [d.arguments for d in named(PROXY_TREE, "auth_request")] == [("/_broadcast_access",)]
    assert named(PROXY_TREE, "satisfy") == [], "the refusal of a method and the API's both hold"

    [types] = [directive for directive in HLS.block if directive.name == "types"]
    assert {kind.name: kind.arguments for kind in types.block} == {
        "application/vnd.apple.mpegurl": ("m3u8",),
        "video/mp2t": ("ts",),
    }
    assert HLS.inner("add_header") == [("Cache-Control", "no-store", "always")]
    assert HLS.inner("limit_req") == location_of(SERVER.block, "/api/").inner("limit_req")
    assert HLS.inner("limit_conn") == []
    assert HLS.inner("access_log") == [("off",)]
    gzipped = {kind for arguments in SERVER.inner("gzip_types") for kind in arguments}
    assert not gzipped & {"application/vnd.apple.mpegurl", "video/mp2t"}


def test_the_proxy_asks_the_api_about_each_caller_and_remembers_only_a_yes() -> None:
    """Before it serves a playlist or a segment the proxy asks the API's own operation
    whether this caller may watch this broadcast. No browser reaches the location. It
    sets no request header and sends no body, so the API judges the caller by the two
    identity headers the viewer's request came with (a test above holds that), as it
    judges every call.

    A yes is remembered per broadcast and caller, as the proxy names the caller to the
    API, for the seconds `.env` says: a viewer costs the API one question in that time,
    and a remembered yes is never another caller's. Nothing but a 200 is remembered,
    and nothing overrides what the API's answer says about keeping it."""
    assert "get" in API_PATHS["/api/broadcasts/{broadcast_id}/access"]
    assert [(directive.name, directive.arguments) for directive in ACCESS.block] == [
        ("internal", ()),
        ("proxy_pass_request_body", ("off",)),
        ("proxy_cache", ("broadcast_access",)),
        ("proxy_cache_key", (f"$broadcast_of_request {API_CALLER}",)),
        ("proxy_cache_valid", ("200", "${NGINX_BROADCAST_ACCESS_CACHE_SECONDS}s")),
        ("proxy_cache_lock", ("on",)),
        ("proxy_pass", ("http://$api_upstream/api/broadcasts/$broadcast_of_request/access",)),
    ]
    [kept] = [directive for directive in PROXY_TREE if directive.name == "proxy_cache_path"]
    assert kept.arguments[0].startswith("/var/cache/nginx/")
    assert "keys_zone=broadcast_access:1m" in kept.arguments
    assert [d.arguments for d in named(PROXY_TREE, "proxy_cache")] == [("broadcast_access",)]
    assert [d.arguments[0] for d in named(PROXY_TREE, "proxy_cache_valid")] == ["200"]
    for word in ("proxy_ignore_headers", "proxy_cache_use_stale", "proxy_cache_methods"):
        assert named(PROXY_TREE, word) == [], word
    assert named(IDENTITY_TREE, "proxy_cache") == []


def test_what_nginx_answers_for_video_has_the_error_shape_of_the_api() -> None:
    """The API's answer to the proxy's question reaches the viewer as its status alone,
    so nginx writes the body: the API's own for a caller nobody named, the one sentence
    the API has for a live stream a caller may not watch, and "not found" for a file
    that is not there. A request above the limits and an API that does not answer are
    answered as everywhere under /api/. None of these locations passes anything on or
    counts anything."""
    handler = answers_of_nginx(HLS)
    assert set(handler) == {401, 403, 404, 413, 429, 500, 502, 503, 504}
    assert answer_of(handler[401]) == (
        UnauthenticatedError.status_code,
        {
            "code": ErrorCode.UNAUTHENTICATED,
            "message": UnauthenticatedError.default_message,
            "details": {},
        },
    )
    assert answer_of(handler[403]) == (
        ForbiddenError.status_code,
        {"code": ErrorCode.FORBIDDEN, "message": NOT_AVAILABLE_MESSAGE, "details": {}},
    )
    assert answer_of(handler[404]) == (
        NotFoundError.status_code,
        {"code": ErrorCode.NOT_FOUND, "message": NotFoundError.default_message, "details": {}},
    )
    everywhere = answers_of_nginx(location_of(SERVER.block, "/api/"))
    assert handler[413] is everywhere[413]
    assert handler[429] is everywhere[429]
    for status in (500, 502, 503, 504):
        assert handler[status] is everywhere[502], status
    for answer in handler.values():
        assert [directive.name for directive in answer.block if directive.name != "add_header"] == [
            "types",
            "default_type",
            "return",
        ], answer.arguments
        answer_of(answer)
    of_video_alone = {"@api_unauthenticated", "@api_not_found", "@broadcast_refused"}
    for location in API_LOCATIONS:
        answering = {answer.arguments[0] for answer in answers_of_nginx(location).values()}
        assert not answering & of_video_alone, location.arguments


@pytest.mark.parametrize("environment", [EXAMPLE, DEPLOYED], ids=["example", "production"])
def test_the_content_security_policy_lets_the_app_play_and_send_video_of_its_own_origin(
    environment: Mapping[str, str],
) -> None:
    """A player gets the playlist from this origin, or builds the picture from segments
    it fetched itself, which is a `blob:` address (`media-src`); the uploader, the event
    stream and the player fetch from this origin (`connect-src`); the player's script
    is the app's own (`script-src`). No source of the policy is another host. The proxy
    sends no `Permissions-Policy`, so camera and microphone are the page's to ask for."""
    policy = {
        words[0]: words[1:]
        for words in (
            part.split() for part in environment["NGINX_CONTENT_SECURITY_POLICY"].split(";")
        )
        if words
    }
    assert policy["media-src"] == ["'self'", "blob:"]
    assert policy["img-src"] == ["'self'", "data:", "blob:"]
    assert "'self'" in policy["connect-src"]
    assert policy["script-src"][0] == "'self'"
    assert policy["default-src"] == ["'self'"]
    for directive, sources in policy.items():
        for source in sources:
            assert source.startswith("'") or source.endswith(":"), f"{directive}: {source}"
    sent = [
        directive.arguments[0].lower()
        for directive in (*all_of(PROXY_TREE), *all_of(IDENTITY_TREE))
        if directive.name == "add_header"
    ]
    assert "permissions-policy" not in sent


# --- nginx's own verdict ----------------------------------------------------------------

NGINX_TEST_SECONDS: Final[int] = 180
"""Room for Docker to fetch the nginx image where it is not there yet."""

DOCKER_ITSELF_FAILED: Final[int] = 125
"""The exit status of `docker run` when Docker could not run the container at all: no
daemon, or no image and no way to fetch one. What nginx says comes with its own status."""


def run_without_network(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """`docker run --rm --network none <arguments>`: a container that reaches nothing,
    publishes nothing and is removed when its command has ended. Without Docker, or
    where Docker cannot run the container, the test that asks is skipped."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is not installed: the image was not asked")
    command = [docker, "run", "--rm", "--network", "none", *arguments]
    try:
        asked = subprocess.run(
            command, capture_output=True, text=True, timeout=NGINX_TEST_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"Docker did not answer within {NGINX_TEST_SECONDS} seconds")
    if asked.returncode == DOCKER_ITSELF_FAILED:
        pytest.skip(f"Docker could not run the image: {asked.stderr.strip()[-200:]}")
    return asked


@pytest.mark.parametrize(
    ("environment", "with_the_overlay"),
    [(EXAMPLE, False), (DEPLOYED, False), (EXAMPLE, True)],
    ids=["example", "production", "development"],
)
def test_nginx_accepts_the_configuration_as_the_stack_renders_it(
    environment: Mapping[str, str], with_the_overlay: bool
) -> None:
    """`nginx -t`, asked of the image the stack runs. The container is handed what the
    compose files hand the nginx service, its NGINX_* variables and its mounts, so the
    image's own start-up renders the templates exactly as it does in the stack, and
    nginx then reads the result the way it would at start. A directive it does not know,
    a variable nobody defines, a value it cannot read as an address: each of them would
    otherwise show when the proxy of a deployment fails to come up.

    The volume of the media box is not among the mounts: nginx reads it when a request
    asks for a file of it, and nothing of it at start.

    The container has no network, publishes nothing and is removed when nginx has
    answered. Without Docker the test is skipped.
    """
    handed = handed_to_nginx(STACK["nginx"], environment)
    mounts = mounts_of(STACK["nginx"])
    if with_the_overlay:
        handed |= handed_to_nginx(OVERLAY["nginx"], environment)
        mounts |= mounts_of(OVERLAY["nginx"])
    arguments: list[str] = []
    for name, value in sorted(handed.items()):
        arguments += ["--env", f"{name}={value}"]
    for source, target in mounts.items():
        arguments += ["--volume", f"{(REPO_ROOT / source).resolve()}:{target}:ro"]
    arguments += [scalar(STACK["nginx"], "image"), "nginx", "-t"]

    asked = run_without_network(arguments)

    assert asked.returncode == 0, asked.stderr
    assert "test is successful" in asked.stderr


def test_the_image_of_the_proxy_gives_a_worker_the_connections_the_stack_counts_on() -> None:
    """`WORKER_CONNECTIONS` is a figure of the image: the `nginx.conf` it ships, which
    is the one the stack runs, because the stack renders into `conf.d` alone. The image
    is asked for that file. It gives a worker that many connections, starts one worker
    per CPU, and loads what lies in `conf.d` inside its `http` block, where the proxy's
    maps and zones belong.

    Without Docker the test is skipped.
    """
    asked = run_without_network([NGINX_IMAGE, "cat", "/etc/nginx/nginx.conf"])

    assert asked.returncode == 0, asked.stderr
    shipped = parse(asked.stdout)
    assert [d.arguments for d in named(shipped, "worker_connections")] == [
        (str(WORKER_CONNECTIONS),)
    ]
    assert [d.arguments for d in named(shipped, "worker_processes")] == [("auto",)]
    assert named(shipped, "worker_rlimit_nofile") == [], "a worker has the limit of its container"
    [http] = [directive for directive in shipped if directive.name == "http"]
    assert http.inner("include")[-1] == (f"{LOADED_BY_THE_IMAGE}/*.conf",)
