"""The stack files (SPEC §9, A.10): `compose.yaml`, `compose.override.yaml` and the proxy
configuration under `infra/nginx/`.

Nothing here starts a container. The tests read the files and hold the rules whose
breach nobody would notice until it mattered: who may tell the API who is calling and
who is told, which service can reach which, who is handed the owner role's password,
that the deployed stack carries nothing of the development one, what the proxy writes
into its log, and that every value still comes from `.env`.

The compose files are read as text, the way they are written: two spaces per level, a
comment always on a line of its own. The nginx files are parsed into their directives.
"""

import ipaddress
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from app.api.middleware import ORIGIN_MESSAGE
from app.core.config import INFRA_ONLY_VARIABLES, REPO_ROOT
from app.core.errors import ErrorCode, ForbiddenError, RateLimitedError, ServiceUnavailableError
from tests.support.containers import POSTGRES_IMAGE, REDIS_IMAGE, SCYLLA_COMMAND, SCYLLA_IMAGE

PROXY_DIRECTORY: Final[Path] = REPO_ROOT / "infra" / "nginx" / "conf.d"
DEVELOPMENT_DIRECTORY: Final[Path] = REPO_ROOT / "infra" / "nginx" / "dev"

COMPOSE: Final[str] = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
OVERRIDE: Final[str] = (REPO_ROOT / "compose.override.yaml").read_text(encoding="utf-8")
EXAMPLE_ENV: Final[str] = (REPO_ROOT / ".example.env").read_text(encoding="utf-8")
MAKEFILE: Final[str] = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
BACKEND_DOCKERFILE: Final[str] = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
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


def mounts_of(service: str) -> dict[str, PurePosixPath]:
    """Host path to container path of the mounts of a service.

    Each of them has to be a read-only bind mount that never creates its source: a path
    that is missing then stops the container instead of mounting an empty directory.
    """
    found = re.search(r"^    volumes:\n((?:      .*\n)+)", service, re.MULTILINE)
    assert found is not None
    mounts = {source: PurePosixPath(target) for source, target in BIND_MOUNT.findall(found[1])}
    assert len(mounts) == len(re.findall(r"^      - ", found[1], re.MULTILINE)), found[1]
    return mounts


def passed_to_nginx(service: str) -> set[str]:
    """The NGINX_* variables a compose file hands to the nginx container."""
    return set(re.findall(r"^      (NGINX_[A-Z0-9_]+):", service, re.MULTILINE))


def networks_of(compose: str) -> list[str]:
    """The networks a compose file defines, in its top-level `networks:` block."""
    found = re.search(r"^networks:\n((?:  .*\n)+)", code_of(compose), re.MULTILINE)
    if found is None:
        return []
    return re.findall(r"^  ([a-z][a-z0-9-]*):", found.group(1), re.MULTILINE)


STACK: Final[dict[str, str]] = services_of(COMPOSE)
OVERLAY: Final[dict[str, str]] = services_of(OVERRIDE)


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


def placeholders(template: str) -> set[str]:
    """The `${NGINX_*}` placeholders the nginx image fills in when it renders a template."""
    return set(re.findall(r"\$\{(NGINX_[A-Z0-9_]+)\}", template))


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


def looked_up(variable: str, headers: Mapping[str, str]) -> str:
    """What the maps of the proxy make of `variable` for a request with these headers.

    A header the request does not carry is the empty string, as it is for nginx. A map
    is read the way nginx reads one: a source value that is written out wins, whatever
    its case, then the first regular expression that matches, then `default`. A result
    that is a variable is looked up in turn.
    """
    sent = {request_variable(name): value for name, value in headers.items()}
    if variable.startswith("$http_"):
        return sent.get(variable, "")
    rules = maps_to(variable)
    source = looked_up(rules.arguments[0], headers)
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
    return looked_up(result, headers) if result.startswith("$") else result


def answer_of(location: Directive) -> tuple[int, dict[str, object]]:
    """Status and JSON body a named location of the proxy answers with."""
    assert location.inner("default_type") == [("application/json",)]
    [(status, body)] = location.inner("return")
    parsed: dict[str, object] = json.loads(body)
    return int(status), parsed


def upstream_of(location: Directive) -> str:
    """The upstream a location passes its requests to, as the variable that names it."""
    [(target,)] = location.inner("proxy_pass")
    return target.removeprefix("http://")


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
"""The locations that pass requests to the API: the `/api/` prefix and the event streams."""


# --- compose.yaml alone is the deployed stack -----------------------------------------


def test_compose_yaml_alone_knows_nothing_of_the_development_identity() -> None:
    """Comments included: the file names nothing that exists on a laptop only."""
    deployed = COMPOSE.lower()
    for word in ("infra/nginx/dev", "templates/dev", "dev_user", "__dev", "identity"):
        assert word not in deployed, word


def test_compose_yaml_alone_knows_nothing_of_mail() -> None:
    assert re.findall(r"mailpit|smtp|\bmail", COMPOSE, re.IGNORECASE) == []


@pytest.mark.parametrize(
    "compose", [COMPOSE, OVERRIDE], ids=["compose.yaml", "compose.override.yaml"]
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
    read_by_compose = {name for name, _ in references(COMPOSE) + references(OVERRIDE)}
    for name in sorted(INFRA_ONLY_VARIABLES):
        read = name in read_by_compose or re.search(rf"\b{name}\b", MAKEFILE) is not None
        assert read, f"{name} is in .example.env and nothing reads it"


def test_every_service_has_a_memory_limit_from_the_env() -> None:
    for name, service in STACK.items():
        limit = scalar(service, "mem_limit")
        assert re.fullmatch(r"\$\{[A-Z_]+_MEMORY_LIMIT:\?\}", limit), f"{name}: {limit}"


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
    """One revision, no upgrade path: a database built from other models must stop here."""
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


def test_the_web_app_shares_a_network_with_the_proxy_alone() -> None:
    """Two networks: `edge` holds nginx and web, `core` holds nginx, the API and what the
    API works with. The web app is on no network with the API or a store, so it cannot
    open a connection to any of them: Redis and ScyllaDB ask for no password, and the
    API takes its caller from two request headers."""
    assert sorted(networks_of(COMPOSE)) == ["core", "edge"]
    joined = {name: entries(service, "networks") for name, service in STACK.items()}
    assert joined.pop("web") == ["edge"]
    assert joined.pop("nginx") == ["edge", "core"]
    assert set(joined) == {"api", "relay", "migrate", "postgres", "redis", "scylla"}
    for name, networks in joined.items():
        assert networks == ["core"], name


def test_nothing_reaches_around_the_two_networks() -> None:
    """No service joins the host's network or another container's, none is linked to
    another, and the overlay leaves every service on the networks it has."""
    for compose in (COMPOSE, OVERRIDE):
        for word in ("network_mode", "links:", "external", "extra_hosts"):
            assert word not in code_of(compose), word
    assert networks_of(OVERRIDE) == []
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

    assert "POSTGRES_PASSWORD" not in code_of(OVERRIDE)
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
    as a second line, and a request without them is refused there."""
    assert len(API_LOCATIONS) == 2
    to_the_api = [
        (location, headers)
        for location, headers in proxied_locations(SERVER)
        if upstream_of(location) == "$api_upstream"
    ]
    assert [location for location, _ in to_the_api] == API_LOCATIONS
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
    assert len(to_the_web) + len(API_LOCATIONS) == len(named(PROXY_TREE, "proxy_pass"))
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


def test_the_switcher_offers_the_admin_and_the_barista_of_the_example_env() -> None:
    offered = set(re.findall(r'data-id="([^"]+)"', SWITCHER))
    assert listed("ADMIN_EMAILS")
    assert listed("BARISTA_EMAILS")
    for address in (*listed("ADMIN_EMAILS"), *listed("BARISTA_EMAILS"), EXAMPLE["DEV_USER_ID"]):
        assert address in offered, address


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


def test_the_body_limit_comes_from_the_env_and_holds_for_every_location() -> None:
    assert SERVER.inner("client_max_body_size") == [("${NGINX_CLIENT_MAX_BODY_SIZE}",)]
    assert len(named(PROXY_TREE, "client_max_body_size")) == 1
    assert named(IDENTITY_TREE, "client_max_body_size") == []


def test_the_visitors_address_is_the_last_entry_of_the_portals_header() -> None:
    """Whatever stands before the last entry was supplied by the client: no recursion."""
    assert [d.arguments for d in named(PROXY_TREE, "real_ip_header")] == [
        ("X-Original-Forwarded-For",)
    ]
    assert [d.arguments for d in named(PROXY_TREE, "real_ip_recursive")] == [("off",)]


def test_address_and_scheme_are_believed_from_the_same_private_peers_only() -> None:
    for_the_address = {d.arguments[0] for d in PROXY_TREE if d.name == "set_real_ip_from"}
    [peers] = [directive for directive in PROXY_TREE if directive.name == "geo"]
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


def test_the_outer_limits_count_per_signed_in_user() -> None:
    """Visitors behind one address must not share a bucket. A request that names nobody
    is counted by its address."""
    [callers] = [
        directive
        for directive in PROXY_TREE
        if directive.name == "map" and directive.arguments[0] == request_variable(ID_HEADER)
    ]
    caller = callers.arguments[1]
    assert callers.inner("default") == [("$binary_remote_addr",)]
    [requests] = named(PROXY_TREE, "limit_req_zone")
    [streams] = named(PROXY_TREE, "limit_conn_zone")
    assert requests.arguments[0] == caller
    assert requests.arguments[2] == "rate=${NGINX_RATE_LIMIT}"
    assert streams.arguments[0] == caller
    assert len(API_LOCATIONS) == 2
    for location in API_LOCATIONS:
        [(zone, burst, _)] = location.inner("limit_req")
        assert zone == requests.arguments[1].split(":")[0], location.arguments
        assert burst == "burst=${NGINX_RATE_LIMIT_BURST}", location.arguments


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

    assert looked_up("$foreign_request", headers) == foreign


def test_both_api_locations_refuse_a_foreign_request_before_they_count_it() -> None:
    """Another team's page could otherwise use up the request limit, or the streams, of
    whoever looks at it. `return` belongs to the phase in which nginx rewrites a
    request, which is over before `limit_req` and `limit_conn` see it; the named
    location that answers has no limit of its own and inherits none."""
    assert len(API_LOCATIONS) == 2
    for location in API_LOCATIONS:
        refusal = location.block[0]
        assert (refusal.name, refusal.arguments) == ("if", ("($foreign_request)",))
        assert [(inside.name, inside.arguments) for inside in refusal.block] == [
            ("return", ("403",))
        ]
        assert location.inner("limit_req"), location.arguments
        answer = answers_of_nginx(location)[403]
        assert answer.arguments == ("@api_forbidden",)
        assert named(answer.block, "limit_req") + named(answer.block, "limit_conn") == []
    assert len(named(PROXY_TREE, "if")) == len(API_LOCATIONS)
    assert SERVER.inner("limit_req") + SERVER.inner("limit_conn") == []
    assert [d.name for d in PROXY_TREE if d.name in {"limit_req", "limit_conn"}] == []


def test_event_streams_are_unbuffered_patient_and_limited_per_caller() -> None:
    """Opening a stream counts against the caller's request limit like any other call,
    and a caller holds a limited number of streams open at once."""
    [stream] = [d for d in SERVER.block if d.name == "location" and d.arguments[0] == "~"]
    assert stream in API_LOCATIONS
    path = re.compile(stream.arguments[1])
    assert path.search("/api/markets/0190f1a2/stream")
    assert not path.search("/api/markets/0190f1a2/history")
    assert not path.search("/markets/0190f1a2/stream")
    [streams] = named(PROXY_TREE, "limit_conn_zone")
    zone = streams.arguments[1].removeprefix("zone=").split(":")[0]
    assert stream.inner("limit_conn") == [(zone, "${NGINX_SSE_STREAMS_PER_CALLER}")]
    assert stream.inner("proxy_buffering") == [("off",)]
    assert stream.inner("proxy_read_timeout") == [("${NGINX_SSE_READ_TIMEOUT}",)]
    assert stream.inner("proxy_pass_header") == [("X-Accel-Buffering",)]
    assert stream.inner("limit_req") == location_of(SERVER.block, "/api/").inner("limit_req")


def test_the_access_log_holds_no_query_string_no_referer_and_no_cookie() -> None:
    """A code to redeem arrives as `/wallet?code=...`, the Referer of the requests that
    page makes repeats it, and a cookie of the portal is a visitor's sign-in."""
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
    """Whether the portal hands its session cookie on to a team's VM is an open check
    (docs/SERVER_INFRASTRUCTURE.md §13). The log answers it with one word per request,
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
    with the words the API itself uses (SPEC A.2)."""
    under_api = [
        location
        for location in SERVER.block
        if location.name == "location" and "/api/" in location.arguments[-1]
    ]
    assert under_api == API_LOCATIONS
    for location in under_api:
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
