"""Settings: the typed view of the repository's `.env` file.

Rules (see also the header of `.example.env`):

* Every field names its environment variable explicitly, so grepping a variable name
  finds exactly one line here.
* No field has a default. The `.env` file is the single source of truth and a missing
  variable fails startup instead of silently falling back.
* The environment speaks CR; fields typed `CreditMicro` are converted to µCR on load and
  are named `*_micro`.

To add a setting: add it to `.env` and `.example.env`, add a field to the matching
section below, and use it through `Settings`. `tests/unit/test_settings.py` fails if the
three drift apart.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, Self
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.enums import MarketTier
from app.core.identity import USERNAME_PATTERN, USERNAME_STORED_MAX_LENGTH, portal_address
from app.core.live import TOPICS_HARD_MAX
from app.core.text import (
    ANSWER_STORED_MAX_LENGTH,
    CHAT_BACKLOG_MAX,
    CHAT_MESSAGE_STORED_MAX_LENGTH,
    QUESTION_STORED_MAX_LENGTH,
)
from app.core.units import MICRO, PrecisionError, to_micro

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE: Final[Path] = REPO_ROOT / ".env"

INFRA_ONLY_VARIABLES: Final[frozenset[str]] = frozenset(
    {
        "NGINX_HTTP_BIND",
        "NGINX_HTTP_PORT",
        "API_HOST_PORT",
        "WEB_HOST_PORT",
        "EDGE_SUBNET",
        "EDGE_GATEWAY",
        "NGINX_MEMORY_LIMIT",
        "WEB_MEMORY_LIMIT",
        "API_MEMORY_LIMIT",
        "BACKEND_WORKER_MEMORY_LIMIT",
        "POSTGRES_MEMORY_LIMIT",
        "REDIS_MEMORY_LIMIT",
        "SCYLLA_MEMORY_LIMIT",
        "CONTAINER_LOG_MAX_SIZE",
        "CONTAINER_LOG_MAX_FILES",
        "API_SHUTDOWN_TIMEOUT_SECONDS",
        "API_NOFILE_LIMIT",
        "NGINX_NOFILE_LIMIT",
        "MEDIA_MEMORY_LIMIT",
        "MEDIA_CPUS",
        "MEDIA_PIDS_LIMIT",
        "MEDIA_HLS_TMPFS_SIZE",
        "MEDIA_FFMPEG_THREADS",
        "MEDIA_X264_PRESET",
        "SCYLLA_SMP",
        "SCYLLA_MEMORY",
        "NGINX_CLIENT_MAX_BODY_SIZE",
        "NGINX_RATE_LIMIT",
        "NGINX_RATE_LIMIT_BURST",
        "NGINX_SSE_READ_TIMEOUT",
        "NGINX_SSE_STREAMS_PER_CALLER",
        "NGINX_BROADCAST_UPLOADS_PER_CALLER",
        "NGINX_CONTENT_SECURITY_POLICY",
        "DEV_USER_ID",
        "DEV_USER_NAME",
        "DEPLOY_SSH_HOST",
        "DEPLOY_PATH",
    }
)
"""Variables in `.env` that only Compose, nginx, the media box and the Makefile read, never
the Python code of the API."""

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_HEADER_NAME = re.compile(r"[A-Za-z0-9]+(-[A-Za-z0-9]+)*")
_USERNAME = re.compile(USERNAME_PATTERN)

_NOT_IN_A_SLUG = re.compile(r"[^a-z0-9]+")

CODE_MAX_LENGTH: Final[int] = 32
"""The most characters `CODE_LENGTH` may give a code, which is also the width of
`codes.code`."""

PRODUCT_ID_MAX_LENGTH: Final[int] = 64
"""Longest id of a product, which is also the width of `vouchers.product_id`."""

PRODUCT_NAME_MAX_LENGTH: Final[int] = 100
"""Longest name of a product, which is also the width of `vouchers.product_name`."""

MILESTONE_MAX_DAYS: Final[int] = 9999
"""The longest streak `STREAK_MILESTONES` may name: the bound of a format, four digits."""

BADGE_NAME_MAX_LENGTH: Final[int] = 20
"""Longest name of a badge in `BADGE_LEVELS`. A badge is a word or two."""

BADGE_MAX_WINS: Final[int] = 1_000_000
"""The most wins an entry of `BADGE_LEVELS` may start at."""

SHARE_PRESETS_MAX: Final[int] = 4
"""The most shortcuts `TRADE_SHARE_PRESETS` may name: what one row of the trade sheet holds."""


@dataclass(frozen=True, slots=True)
class VoucherProduct:
    """One entry of `VOUCHER_PRODUCTS`: something a voucher can be for."""

    id: str
    """The slug of the name: "Iced Tea" is `iced-tea`, "M&Ms" is `m-ms`. It is what a
    request names and what a voucher row keeps."""

    name: str
    """As written in `.env`, and shown as it is."""

    price_micro: int
    """µCR a voucher for it takes. Above zero."""


@dataclass(frozen=True, slots=True)
class StreakMilestone:
    """One entry of `STREAK_MILESTONES`: a bonus for a streak of a length."""

    days: int
    """The length a streak has to have, in days. From 2 to `MILESTONE_MAX_DAYS`."""

    bonus_micro: int
    """µCR booked once per player, the first time their streak is that long. Above zero."""


@dataclass(frozen=True, slots=True)
class BadgeLevel:
    """One entry of `BADGE_LEVELS`: the badge of a player with that many wins."""

    level: int
    """Its position in the list, from 1."""

    name: str
    """As written in `.env`, and shown as it is."""

    wins: int
    """The wins it starts at. 0 for the first entry."""


def _csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_csv(value: object) -> object:
    return _csv_items(value) if isinstance(value, str) else value


def _cr_to_micro(value: object) -> int:
    """Parse a CR amount from the environment into µCR."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("expected a CR amount such as 1 or 0.25")
    try:
        return to_micro(Decimal(str(value).strip()))
    except (InvalidOperation, PrecisionError) as exc:
        raise ValueError(f"not a valid CR amount: {value!r}") from exc


def _cr_list_to_micro(value: object) -> object:
    """Parse a comma-separated list of CR amounts into µCR."""
    items = _split_csv(value)
    if not isinstance(items, list):
        raise ValueError("expected a comma-separated list of CR amounts")
    return [_cr_to_micro(item) for item in items]


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("must be a lowercase SQL identifier (letters, digits, underscore)")
    return value


def _header_name(value: str) -> str:
    # No underscore: proxies drop header names that carry one.
    if not _HEADER_NAME.fullmatch(value):
        raise ValueError("must be a header name of letters and digits joined by single dashes")
    return value


def _slug(name: str) -> str:
    """The id of a product: its name in lowercase ASCII letters and digits, with one dash
    for every run of anything else and none at either end."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return _NOT_IN_A_SLUG.sub("-", ascii_name.lower()).strip("-")


def _products(value: object) -> object:
    """Parse `Name:price,Name:price` into products, in the order written.

    The price is what follows the last colon, so a name may hold one. Two names with one
    slug are refused: the id would not say which of them is meant.
    """
    if not isinstance(value, str):
        return value
    products: list[VoucherProduct] = []
    for item in _csv_items(value):
        name, separator, price = item.rpartition(":")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"expected Name:price, got {item!r}")
        if len(name) > PRODUCT_NAME_MAX_LENGTH:
            raise ValueError(f"the name {name!r} is longer than {PRODUCT_NAME_MAX_LENGTH}")
        try:
            price_micro = _cr_to_micro(price)
        except ValueError as exc:
            raise ValueError(f"the price of {name!r} is {exc}") from exc
        if price_micro <= 0:
            raise ValueError(f"the price of {name!r} must be above zero")
        product_id = _slug(name)
        if not product_id:
            raise ValueError(f"the name {name!r} has no letter or digit to make an id of")
        if len(product_id) > PRODUCT_ID_MAX_LENGTH:
            raise ValueError(f"the id of {name!r} is longer than {PRODUCT_ID_MAX_LENGTH}")
        if any(product.id == product_id for product in products):
            raise ValueError(f"{name!r} has the id of another product, {product_id!r}")
        products.append(VoucherProduct(id=product_id, name=name, price_micro=price_micro))
    return products


def _whole_number(text: str, what: str) -> int:
    """`text` as a whole number written in ASCII digits and nothing else."""
    if not (text.isascii() and text.isdigit()):
        raise ValueError(f"{what} is not a whole number: {text!r}")
    return int(text)


def _milestones(value: object) -> object:
    """Parse `days:bonus,days:bonus` into milestones. An empty text is no milestone.

    Days are whole numbers from 2 to `MILESTONE_MAX_DAYS` in strictly ascending order,
    which also refuses a repeat. A bonus is a CR amount above zero.
    """
    if not isinstance(value, str):
        return value
    milestones: list[StreakMilestone] = []
    for item in _csv_items(value):
        days_text, separator, bonus = item.partition(":")
        if not separator or ":" in bonus:
            raise ValueError(f"expected days:bonus, got {item!r}")
        days = _whole_number(days_text.strip(), f"the days of {item!r}")
        if not 2 <= days <= MILESTONE_MAX_DAYS:
            raise ValueError(f"the days of {item!r} must be from 2 to {MILESTONE_MAX_DAYS}")
        try:
            bonus_micro = _cr_to_micro(bonus)
        except ValueError as exc:
            raise ValueError(f"the bonus of {item!r} is {exc}") from exc
        if bonus_micro <= 0:
            raise ValueError(f"the bonus of {item!r} must be above zero")
        if milestones and days <= milestones[-1].days:
            raise ValueError(f"the days of {item!r} must be above those of the entry before it")
        milestones.append(StreakMilestone(days=days, bonus_micro=bonus_micro))
    return tuple(milestones)


def _badge_levels(value: object) -> object:
    """Parse `Name:wins,Name:wins` into badges, in the order written.

    The wins are what follows the last colon, so a name may hold one. The first entry
    starts at 0 wins, so every player has a badge, and the wins ascend strictly. Two
    names that differ only in case are refused: on screen they are one word.
    """
    if not isinstance(value, str):
        return value
    levels: list[BadgeLevel] = []
    for item in _csv_items(value):
        name, separator, wins_text = item.rpartition(":")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"expected Name:wins, got {item!r}")
        if len(name) > BADGE_NAME_MAX_LENGTH:
            raise ValueError(f"the name {name!r} is longer than {BADGE_NAME_MAX_LENGTH}")
        if any(unicodedata.category(character) == "Cc" for character in name):
            raise ValueError(f"the name {name!r} holds a control character")
        wins = _whole_number(wins_text.strip(), f"the wins of {name!r}")
        if wins > BADGE_MAX_WINS:
            raise ValueError(f"the wins of {name!r} must be at most {BADGE_MAX_WINS}")
        if any(level.name.casefold() == name.casefold() for level in levels):
            raise ValueError(f"{name!r} is the name of another badge")
        if not levels and wins != 0:
            raise ValueError(f"the first badge, {name!r}, must start at 0 wins")
        if levels and wins <= levels[-1].wins:
            raise ValueError(f"the wins of {name!r} must be above those of the entry before it")
        levels.append(BadgeLevel(level=len(levels) + 1, name=name, wins=wins))
    return tuple(levels)


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
CreditMicro = Annotated[int, BeforeValidator(_cr_to_micro), Field(ge=0)]
CreditMicroList = Annotated[list[int], NoDecode, BeforeValidator(_cr_list_to_micro)]
SqlIdentifier = Annotated[str, BeforeValidator(_identifier)]
HeaderName = Annotated[str, BeforeValidator(_header_name)]
PositiveInt = Annotated[int, Field(gt=0)]
VoucherProductList = Annotated[
    tuple[VoucherProduct, ...], NoDecode, BeforeValidator(_products), Field(min_length=1)
]
StreakMilestoneList = Annotated[tuple[StreakMilestone, ...], NoDecode, BeforeValidator(_milestones)]
BadgeLevelList = Annotated[
    tuple[BadgeLevel, ...], NoDecode, BeforeValidator(_badge_levels), Field(min_length=1)
]


class _Section(BaseSettings):
    """Base of every settings section: read-only, environment first, then the env file."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )


class AppSettings(_Section):
    env: Literal["development", "test", "production"] = Field(validation_alias="APP_ENV")
    log_level: str = Field(validation_alias="LOG_LEVEL")
    timezone_name: str = Field(validation_alias="APP_TIMEZONE")
    public_base_url: str = Field(validation_alias="PUBLIC_BASE_URL")
    allowed_origins: CsvList = Field(validation_alias="ALLOWED_ORIGINS")

    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("timezone_name")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def is_production(self) -> bool:
        return self.env == "production"


class PostgresSettings(_Section):
    host: str = Field(validation_alias="POSTGRES_HOST")
    port: PositiveInt = Field(validation_alias="POSTGRES_PORT")
    database: str = Field(validation_alias="POSTGRES_DB")
    owner_user: SqlIdentifier = Field(validation_alias="POSTGRES_USER")
    owner_password: str = Field(validation_alias="POSTGRES_PASSWORD")
    app_user: SqlIdentifier = Field(validation_alias="POSTGRES_APP_USER")
    app_password: str = Field(validation_alias="POSTGRES_APP_PASSWORD")
    pool_size: PositiveInt = Field(validation_alias="POSTGRES_POOL_SIZE")
    max_overflow: int = Field(validation_alias="POSTGRES_MAX_OVERFLOW", ge=0)
    pool_timeout_seconds: PositiveInt = Field(validation_alias="POSTGRES_POOL_TIMEOUT_SECONDS")
    lock_timeout_ms: PositiveInt = Field(validation_alias="POSTGRES_LOCK_TIMEOUT_MS")

    def _url(self, user: str, password: str) -> str:
        credentials = f"{quote(user, safe='')}:{quote(password, safe='')}"
        return f"postgresql+asyncpg://{credentials}@{self.host}:{self.port}/{self.database}"

    @property
    def app_url(self) -> str:
        """Connection URL of the app role: what the API and the relay use."""
        return self._url(self.app_user, self.app_password)

    @property
    def owner_url(self) -> str:
        """Connection URL of the owner role: migrations only."""
        return self._url(self.owner_user, self.owner_password)


class RedisSettings(_Section):
    host: str = Field(validation_alias="REDIS_HOST")
    port: PositiveInt = Field(validation_alias="REDIS_PORT")
    db: int = Field(validation_alias="REDIS_DB", ge=0)
    timeout_ms: PositiveInt = Field(validation_alias="REDIS_TIMEOUT_MS")
    market_cache_ttl_seconds: PositiveInt = Field(validation_alias="MARKET_CACHE_TTL_SECONDS")

    @property
    def timeout_seconds(self) -> float:
        return self.timeout_ms / 1000


class LiveSettings(_Section):
    """What bounds an event stream: the answer in which the API pushes events to a tab."""

    heartbeat_seconds: PositiveInt = Field(validation_alias="SSE_HEARTBEAT_SECONDS")
    max_streams: PositiveInt = Field(validation_alias="LIVE_MAX_STREAMS")
    stream_max_seconds: PositiveInt = Field(validation_alias="LIVE_STREAM_MAX_SECONDS")
    retry_ms: PositiveInt = Field(validation_alias="LIVE_RETRY_MS")
    topics_per_stream: int = Field(
        validation_alias="LIVE_TOPICS_PER_STREAM", ge=1, le=TOPICS_HARD_MAX
    )
    queue_events: PositiveInt = Field(validation_alias="LIVE_QUEUE_EVENTS")
    event_max_bytes: int = Field(validation_alias="LIVE_EVENT_MAX_BYTES", ge=512)
    leaderboard_refresh_seconds: PositiveInt = Field(validation_alias="LEADERBOARD_REFRESH_SECONDS")

    @model_validator(mode="after")
    def _a_stream_outlives_two_beats(self) -> Self:
        # A tab calls its stream lost after two missed beats: a stream that is ended
        # sooner than that would never be seen alive.
        if self.stream_max_seconds <= 2 * self.heartbeat_seconds:
            raise ValueError("LIVE_STREAM_MAX_SECONDS must be above twice SSE_HEARTBEAT_SECONDS")
        return self


class ScyllaSettings(_Section):
    hosts: CsvList = Field(validation_alias="SCYLLA_HOSTS")
    port: PositiveInt = Field(validation_alias="SCYLLA_PORT")
    keyspace: SqlIdentifier = Field(validation_alias="SCYLLA_KEYSPACE")
    replication_factor: PositiveInt = Field(validation_alias="SCYLLA_REPLICATION_FACTOR")
    request_timeout_ms: PositiveInt = Field(validation_alias="SCYLLA_REQUEST_TIMEOUT_MS")
    outbox_batch_size: PositiveInt = Field(validation_alias="OUTBOX_BATCH_SIZE")
    outbox_poll_interval_ms: PositiveInt = Field(validation_alias="OUTBOX_POLL_INTERVAL_MS")
    # A chart keeps its first and its latest point, so it has at least those two.
    history_max_points: int = Field(validation_alias="HISTORY_MAX_POINTS", ge=2)
    history_read_limit: PositiveInt = Field(validation_alias="HISTORY_READ_LIMIT")

    @model_validator(mode="after")
    def _reads_at_least_what_it_answers(self) -> Self:
        if self.history_read_limit < self.history_max_points:
            raise ValueError("HISTORY_READ_LIMIT must not be below HISTORY_MAX_POINTS")
        return self

    @property
    def request_timeout_seconds(self) -> float:
        return self.request_timeout_ms / 1000

    @property
    def outbox_poll_interval_seconds(self) -> float:
        return self.outbox_poll_interval_ms / 1000


class IdentitySettings(_Section):
    header_user_id: HeaderName = Field(validation_alias="IDENTITY_HEADER_USER_ID")
    header_user_name: HeaderName = Field(validation_alias="IDENTITY_HEADER_USER_NAME")
    allowed_email_domains: CsvList = Field(validation_alias="ALLOWED_EMAIL_DOMAINS", min_length=1)
    # The ids of the bootstrap admins, each as `portal_address` reads it: an address at
    # an allowed domain, in lower case. An entry that rule refuses fails loading.
    admin_emails: CsvList = Field(validation_alias="ADMIN_EMAILS")
    username_min_length: PositiveInt = Field(validation_alias="USERNAME_MIN_LENGTH")
    username_max_length: int = Field(
        validation_alias="USERNAME_MAX_LENGTH", ge=1, le=USERNAME_STORED_MAX_LENGTH
    )
    reserved_usernames: CsvList = Field(validation_alias="RESERVED_USERNAMES")
    upkeep_interval_seconds: PositiveInt = Field(validation_alias="UPKEEP_INTERVAL_SECONDS")
    # 0: a programme changes as often as its owner likes.
    programme_change_cooldown_days: int = Field(
        validation_alias="PROGRAMME_CHANGE_COOLDOWN_DAYS", ge=0, le=365
    )

    @field_validator("allowed_email_domains")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @field_validator("admin_emails")
    @classmethod
    def _ids(cls, value: list[str], info: ValidationInfo) -> list[str]:
        """Read each bootstrap admin the way an id of the portal is read.

        An entry that is no address at an allowed domain would make nobody an admin,
        so it is refused here; the error names the variable it stands in.
        """
        allowed_domains = info.data.get("allowed_email_domains")
        if allowed_domains is None:
            # ALLOWED_EMAIL_DOMAINS did not load, and that is the error to report.
            return value
        ids: list[str] = []
        for entry in value:
            address = portal_address(entry, allowed_domains)
            if address is None:
                raise ValueError(
                    f"{entry!r} is not an address at {' or '.join(allowed_domains)}, "
                    "so it would make nobody an admin"
                )
            ids.append(address)
        return ids

    @field_validator("reserved_usernames")
    @classmethod
    def _usernames(cls, value: list[str]) -> list[str]:
        """An entry nobody could ask for reserves nothing: most likely a typing mistake."""
        for entry in value:
            if _USERNAME.fullmatch(entry) is None:
                raise ValueError(f"{entry!r} is not a username: {USERNAME_PATTERN}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.username_min_length > self.username_max_length:
            raise ValueError("USERNAME_MIN_LENGTH must not be above USERNAME_MAX_LENGTH")
        return self

    @property
    def upkeep_interval(self) -> timedelta:
        return timedelta(seconds=self.upkeep_interval_seconds)

    @property
    def programme_change_cooldown(self) -> timedelta:
        return timedelta(days=self.programme_change_cooldown_days)


class GrantSettings(_Section):
    signup_grant_micro: CreditMicro = Field(validation_alias="SIGNUP_GRANT_CR")
    monthly_grant_micro: CreditMicro = Field(validation_alias="MONTHLY_GRANT_CR")


class StreakSettings(_Section):
    daily_reward_micro: CreditMicro = Field(validation_alias="STREAK_DAILY_REWARD_CR")
    # In ascending order of their days.
    milestones: StreakMilestoneList = Field(validation_alias="STREAK_MILESTONES")
    lock_timeout_ms: PositiveInt = Field(validation_alias="STREAK_LOCK_TIMEOUT_MS")

    def milestone(self, days: int) -> StreakMilestone | None:
        """The milestone a streak of exactly `days` days reaches, or None."""
        for milestone in self.milestones:
            if milestone.days == days:
                return milestone
        return None


class BadgeSettings(_Section):
    # In ascending order of their wins; the first one starts at 0.
    levels: BadgeLevelList = Field(validation_alias="BADGE_LEVELS")

    def level_for(self, wins: int) -> BadgeLevel:
        """The badge of a player with `wins` wins: the last entry they have reached."""
        reached = [level for level in self.levels if level.wins <= wins]
        return reached[-1] if reached else self.levels[0]

    def next_after(self, level: BadgeLevel) -> BadgeLevel | None:
        """The entry after `level`, or None when it is the last one."""
        return self.levels[level.level] if level.level < len(self.levels) else None


class CodeSettings(_Section):
    length: int = Field(validation_alias="CODE_LENGTH", ge=8, le=CODE_MAX_LENGTH)
    min_value_micro: CreditMicro = Field(validation_alias="CODE_MIN_VALUE_CR")
    max_value_micro: CreditMicro = Field(validation_alias="CODE_MAX_VALUE_CR")
    default_value_micro: CreditMicro = Field(validation_alias="CODE_DEFAULT_VALUE_CR")
    max_issuance_per_month_micro: CreditMicro = Field(
        validation_alias="MAX_CODE_ISSUANCE_PER_MONTH"
    )
    redeem_limit_per_user: PositiveInt = Field(validation_alias="CODE_REDEEM_LIMIT_PER_USER")
    redeem_limit_per_user_window_seconds: PositiveInt = Field(
        validation_alias="CODE_REDEEM_LIMIT_PER_USER_WINDOW_SECONDS"
    )

    @model_validator(mode="after")
    def _values_in_order(self) -> Self:
        # A code worth nothing cannot be stored (`ck_code_batches_value_positive`).
        if self.min_value_micro <= 0:
            raise ValueError("CODE_MIN_VALUE_CR must be above zero")
        if not self.min_value_micro <= self.default_value_micro <= self.max_value_micro:
            raise ValueError(
                "CODE_DEFAULT_VALUE_CR must lie between CODE_MIN_VALUE_CR and CODE_MAX_VALUE_CR"
            )
        return self


class VoucherSettings(_Section):
    # In the order written, which is the order the wallet offers them in.
    products: VoucherProductList = Field(validation_alias="VOUCHER_PRODUCTS")
    code_length: int = Field(validation_alias="VOUCHER_CODE_LENGTH", ge=6, le=32)
    expiry_days: PositiveInt = Field(validation_alias="VOUCHER_EXPIRY_DAYS")
    rescan_seconds: PositiveInt = Field(validation_alias="VOUCHER_RESCAN_SECONDS")

    @property
    def ttl(self) -> timedelta:
        return timedelta(days=self.expiry_days)

    @property
    def rescan_window(self) -> timedelta:
        """How long after redeeming a voucher its barista is shown the redemption again."""
        return timedelta(seconds=self.rescan_seconds)

    @property
    def lowest_price_micro(self) -> int:
        """What the cheapest product takes: the least a balance must hold for a voucher."""
        return min(product.price_micro for product in self.products)

    def product(self, product_id: str) -> VoucherProduct | None:
        """The product with this id, or None if `VOUCHER_PRODUCTS` has no such one."""
        for product in self.products:
            if product.id == product_id:
                return product
        return None


class PricingSettings(_Section):
    tier_tight_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_TIGHT_B")
    tier_default_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_DEFAULT_B")
    tier_medium_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_MEDIUM_B")
    # SPEC §4 computes with 50 digits, and this is the lower bound: with 28 to 32
    # digits, quotes at b = 1,442.695040 CR rounded the wrong way (see
    # app/pricing/lmsr.py, "How many digits are enough"). More digits are fine.
    decimal_precision: int = Field(validation_alias="PRICING_DECIMAL_PRECISION", ge=50)
    max_stake_per_user_market_micro: CreditMicro = Field(
        validation_alias="MAX_STAKE_PER_USER_MARKET"
    )
    min_trade_micro: CreditMicro = Field(validation_alias="MIN_TRADE_CR")
    min_trade_shares_micro: CreditMicro = Field(validation_alias="MIN_TRADE_SHARES")
    stake_presets_micro: CreditMicroList = Field(validation_alias="TRADE_STAKE_PRESETS_CR")
    stake_step_micro: CreditMicro = Field(validation_alias="TRADE_STAKE_STEP_CR")
    # µshares: shares have the scale of credits.
    share_presets_micro: CreditMicroList = Field(validation_alias="TRADE_SHARE_PRESETS")
    share_step_micro: CreditMicro = Field(validation_alias="TRADE_SHARE_STEP")
    price_display_decimals: int = Field(validation_alias="PRICE_DISPLAY_DECIMALS", ge=0, le=4)

    @field_validator("stake_step_micro")
    @classmethod
    def _whole_credits(cls, value: int) -> int:
        # The slider of the trade sheet counts in whole credits (`MarketViewer.buy_slider`).
        if value <= 0 or value % MICRO:
            raise ValueError("TRADE_STAKE_STEP_CR must be a whole number of CR above zero")
        return value

    @field_validator("share_presets_micro")
    @classmethod
    def _whole_shares_ascending(cls, value: list[int]) -> list[int]:
        whole = all(preset > 0 and preset % MICRO == 0 for preset in value)
        ascending = all(low < high for low, high in pairwise(value))
        if not (1 <= len(value) <= SHARE_PRESETS_MAX and whole and ascending):
            raise ValueError(
                "TRADE_SHARE_PRESETS must be one to four whole numbers of shares above "
                "zero, in ascending order"
            )
        return value

    @field_validator("share_step_micro")
    @classmethod
    def _whole_shares(cls, value: int) -> int:
        if value <= 0 or value % MICRO:
            raise ValueError("TRADE_SHARE_STEP must be a whole number of shares above zero")
        return value

    def tier_b_micro(self, tier: MarketTier) -> int:
        """Liquidity parameter b of a fixed tier. CUSTOM has none; it starts from a budget."""
        table = {
            MarketTier.TIGHT: self.tier_tight_b_micro,
            MarketTier.DEFAULT: self.tier_default_b_micro,
            MarketTier.MEDIUM: self.tier_medium_b_micro,
        }
        return table[tier]


class RiskSettings(_Section):
    max_open_markets: PositiveInt = Field(validation_alias="MAX_OPEN_MARKETS")
    max_market_budget_micro: CreditMicro = Field(validation_alias="MAX_MARKET_BUDGET")
    max_aggregate_loss_budget_micro: CreditMicro = Field(
        validation_alias="MAX_AGGREGATE_LOSS_BUDGET"
    )
    max_orders_per_minute: PositiveInt = Field(validation_alias="MAX_ORDERS_PER_MINUTE")
    max_market_lifetime_days: PositiveInt = Field(validation_alias="MAX_MARKET_LIFETIME")
    obligations_alert_threshold_micro: CreditMicro = Field(
        validation_alias="OBLIGATIONS_ALERT_THRESHOLD_CR"
    )
    max_admin_adjustment_micro: CreditMicro = Field(validation_alias="MAX_ADMIN_ADJUSTMENT_CR")

    @property
    def max_market_lifetime(self) -> timedelta:
        return timedelta(days=self.max_market_lifetime_days)


class LifecycleSettings(_Section):
    max_proposals_per_user_per_day: PositiveInt = Field(
        validation_alias="MAX_PROPOSALS_PER_USER_PER_DAY"
    )
    max_resolution_delay_days: PositiveInt = Field(validation_alias="MAX_RESOLUTION_DELAY_DAYS")

    @property
    def max_resolution_delay(self) -> timedelta:
        return timedelta(days=self.max_resolution_delay_days)


class DiscussionSettings(_Section):
    """The questions and answers of a market."""

    question_min_length: int = Field(validation_alias="DISCUSSION_QUESTION_MIN_LENGTH", ge=1)
    question_max_length: int = Field(
        validation_alias="DISCUSSION_QUESTION_MAX_LENGTH", ge=1, le=QUESTION_STORED_MAX_LENGTH
    )
    answer_max_length: int = Field(
        validation_alias="DISCUSSION_ANSWER_MAX_LENGTH", ge=1, le=ANSWER_STORED_MAX_LENGTH
    )
    max_questions_per_market: PositiveInt = Field(
        validation_alias="DISCUSSION_MAX_QUESTIONS_PER_MARKET"
    )
    max_waiting_per_user: PositiveInt = Field(validation_alias="DISCUSSION_MAX_WAITING_PER_USER")
    question_limit_per_user: PositiveInt = Field(
        validation_alias="DISCUSSION_QUESTION_LIMIT_PER_USER"
    )
    question_limit_per_user_window_seconds: PositiveInt = Field(
        validation_alias="DISCUSSION_QUESTION_LIMIT_PER_USER_WINDOW_SECONDS"
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.question_max_length < self.question_min_length:
            raise ValueError(
                "DISCUSSION_QUESTION_MAX_LENGTH must not be below DISCUSSION_QUESTION_MIN_LENGTH"
            )
        if self.max_waiting_per_user > self.max_questions_per_market:
            raise ValueError(
                "DISCUSSION_MAX_WAITING_PER_USER must not be above "
                "DISCUSSION_MAX_QUESTIONS_PER_MARKET"
            )
        return self


class ChatSettings(_Section):
    """The live chat beside a market's live stream."""

    message_max_length: int = Field(
        validation_alias="CHAT_MESSAGE_MAX_LENGTH", ge=1, le=CHAT_MESSAGE_STORED_MAX_LENGTH
    )
    message_limit_per_user: PositiveInt = Field(validation_alias="CHAT_MESSAGE_LIMIT_PER_USER")
    message_limit_per_user_window_seconds: PositiveInt = Field(
        validation_alias="CHAT_MESSAGE_LIMIT_PER_USER_WINDOW_SECONDS"
    )
    room_message_limit: PositiveInt = Field(validation_alias="CHAT_ROOM_MESSAGE_LIMIT")
    room_message_limit_window_seconds: PositiveInt = Field(
        validation_alias="CHAT_ROOM_MESSAGE_LIMIT_WINDOW_SECONDS"
    )
    backlog_size: int = Field(validation_alias="CHAT_BACKLOG_SIZE", ge=1, le=CHAT_BACKLOG_MAX)


BYTES_PER_KBIT_SECOND: Final[int] = 125
"""Bytes one second of a stream of 1 kbit/s holds."""


class BroadcastSettings(_Section):
    """A market's live video. A field is its variable's name in lower case without the
    prefix `BROADCAST_`."""

    max_live: int = Field(validation_alias="BROADCAST_MAX_LIVE", ge=1)
    requests_per_user_per_day: int = Field(
        validation_alias="BROADCAST_REQUESTS_PER_USER_PER_DAY", ge=1
    )
    max_minutes: int = Field(validation_alias="BROADCAST_MAX_MINUTES", ge=1)
    stall_seconds: int = Field(validation_alias="BROADCAST_STALL_SECONDS", ge=10)
    max_viewers: int = Field(validation_alias="BROADCAST_MAX_VIEWERS", ge=1)
    access_cache_seconds: int = Field(
        validation_alias="BROADCAST_ACCESS_CACHE_SECONDS", ge=1, le=60
    )
    chunk_ms: int = Field(validation_alias="BROADCAST_CHUNK_MS", ge=250, le=5000)
    chunk_max_bytes: PositiveInt = Field(validation_alias="BROADCAST_CHUNK_MAX_BYTES")
    upload_backlog_seconds: int = Field(validation_alias="BROADCAST_UPLOAD_BACKLOG_SECONDS", ge=2)
    upload_timeout_ms: int = Field(validation_alias="BROADCAST_UPLOAD_TIMEOUT_MS", ge=1000)
    upload_retry_ms: int = Field(validation_alias="BROADCAST_UPLOAD_RETRY_MS", ge=100)
    restart_window_ms: int = Field(validation_alias="BROADCAST_RESTART_WINDOW_MS", ge=1000)
    wait_notice_ms: int = Field(validation_alias="BROADCAST_WAIT_NOTICE_MS", ge=1000)
    player_retry_ms: int = Field(validation_alias="BROADCAST_PLAYER_RETRY_MS", ge=100)
    capture_video_kbps: PositiveInt = Field(validation_alias="BROADCAST_CAPTURE_VIDEO_KBPS")
    input_max_width: int = Field(validation_alias="BROADCAST_INPUT_MAX_WIDTH", ge=640, le=3840)
    input_max_height: int = Field(validation_alias="BROADCAST_INPUT_MAX_HEIGHT", ge=360, le=2160)
    video_height: int = Field(validation_alias="BROADCAST_VIDEO_HEIGHT", ge=240, le=1080)
    video_fps: int = Field(validation_alias="BROADCAST_VIDEO_FPS", ge=10, le=30)
    video_kbps: int = Field(validation_alias="BROADCAST_VIDEO_KBPS", ge=200, le=4000)
    audio_kbps: int = Field(validation_alias="BROADCAST_AUDIO_KBPS", ge=32, le=192)
    hls_segment_seconds: int = Field(validation_alias="BROADCAST_HLS_SEGMENT_SECONDS", ge=1, le=6)
    hls_playlist_segments: int = Field(
        validation_alias="BROADCAST_HLS_PLAYLIST_SEGMENTS", ge=3, le=20
    )
    media_socket: str = Field(validation_alias="BROADCAST_MEDIA_SOCKET")
    media_timeout_ms: int = Field(validation_alias="BROADCAST_MEDIA_TIMEOUT_MS", ge=100)

    @field_validator("input_max_width", "input_max_height", "video_height")
    @classmethod
    def _even(cls, value: int) -> int:
        # The encoder halves both sides of a picture for its colour planes.
        if value % 2:
            raise ValueError("must be an even number of pixels")
        return value

    @field_validator("media_socket")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not PurePosixPath(value).is_absolute() or value.endswith("/"):
            raise ValueError("must be the absolute path of a socket")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.stall_seconds <= self.upload_backlog_seconds:
            raise ValueError(
                "BROADCAST_STALL_SECONDS must be above BROADCAST_UPLOAD_BACKLOG_SECONDS"
            )
        if self.access_cache_seconds >= self.stall_seconds:
            raise ValueError("BROADCAST_ACCESS_CACHE_SECONDS must be below BROADCAST_STALL_SECONDS")
        if self.capture_video_kbps < self.video_kbps:
            raise ValueError("BROADCAST_CAPTURE_VIDEO_KBPS must not be below BROADCAST_VIDEO_KBPS")
        # A piece of BROADCAST_CHUNK_MS at the capture rates, with as much room again:
        # a recorder does not cut its pieces to the millisecond. Compared in thousandths
        # of a byte, so that nothing is rounded.
        two_pieces_bytes_per_mille = 2 * self.capture_bytes_per_second * self.chunk_ms
        if self.chunk_max_bytes * 1000 < two_pieces_bytes_per_mille:
            raise ValueError(
                "BROADCAST_CHUNK_MAX_BYTES must be at least twice what one piece holds: "
                "(BROADCAST_CAPTURE_VIDEO_KBPS + BROADCAST_AUDIO_KBPS) x 125 x "
                f"BROADCAST_CHUNK_MS / 1000, which makes {-(-two_pieces_bytes_per_mille // 1000)} "
                "bytes"
            )
        return self

    @property
    def capture_bytes_per_second(self) -> int:
        """Bytes a studio records per second, picture and sound together."""
        return (self.capture_video_kbps + self.audio_kbps) * BYTES_PER_KBIT_SECOND

    @property
    def heartbeat_interval(self) -> timedelta:
        """How long after a stored `last_chunk_at` the next piece moves it again: a
        quarter of `BROADCAST_STALL_SECONDS`, so a running broadcast never looks stalled."""
        return timedelta(seconds=self.stall_seconds) / 4

    @property
    def seat_seconds(self) -> int:
        """How long a viewer holds a place without asking again: as long as nginx
        remembers the admission, and as long as a playlist's segments play."""
        return self.access_cache_seconds + self.hls_segment_seconds * self.hls_playlist_segments

    @property
    def upload_backlog_bytes(self) -> int:
        """Bytes of video a studio queues while its connection is slow."""
        return self.upload_backlog_seconds * self.capture_bytes_per_second


class UiSettings(_Section):
    quote_debounce_ms: PositiveInt = Field(validation_alias="QUOTE_DEBOUNCE_MS")
    live_poll_fallback_seconds: PositiveInt = Field(validation_alias="LIVE_POLL_FALLBACK_SECONDS")
    live_announce_spread_ms: int = Field(validation_alias="LIVE_ANNOUNCE_SPREAD_MS", ge=0)
    live_viewer_refresh_seconds: PositiveInt = Field(validation_alias="LIVE_VIEWER_REFRESH_SECONDS")


class Settings(BaseModel):
    """All settings, grouped the way `.env` is grouped."""

    model_config = ConfigDict(frozen=True)

    app: AppSettings
    postgres: PostgresSettings
    redis: RedisSettings
    live: LiveSettings
    scylla: ScyllaSettings
    identity: IdentitySettings
    grants: GrantSettings
    streaks: StreakSettings
    badges: BadgeSettings
    codes: CodeSettings
    vouchers: VoucherSettings
    pricing: PricingSettings
    risk: RiskSettings
    lifecycle: LifecycleSettings
    discussion: DiscussionSettings
    chat: ChatSettings
    broadcast: BroadcastSettings
    ui: UiSettings

    @model_validator(mode="after")
    def _sections_agree(self) -> Self:
        """The rules that hold between two sections, checked once all have loaded."""
        # A character is at most four bytes of UTF-8, and 512 bytes hold the rest of
        # the event around the longest message a chat stores.
        if self.live.event_max_bytes < 4 * self.chat.message_max_length + 512:
            raise ValueError(
                "LIVE_EVENT_MAX_BYTES must be at least 4 x CHAT_MESSAGE_MAX_LENGTH + 512"
            )
        # The streak step of a trade gives up sooner than the trade itself would.
        if self.streaks.lock_timeout_ms > self.postgres.lock_timeout_ms:
            raise ValueError("STREAK_LOCK_TIMEOUT_MS must not be above POSTGRES_LOCK_TIMEOUT_MS")
        return self

    @classmethod
    def load(cls, env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
        """Read the process environment, falling back to `env_file` for unset variables."""
        return cls(
            app=AppSettings(_env_file=env_file),
            postgres=PostgresSettings(_env_file=env_file),
            redis=RedisSettings(_env_file=env_file),
            live=LiveSettings(_env_file=env_file),
            scylla=ScyllaSettings(_env_file=env_file),
            identity=IdentitySettings(_env_file=env_file),
            grants=GrantSettings(_env_file=env_file),
            streaks=StreakSettings(_env_file=env_file),
            badges=BadgeSettings(_env_file=env_file),
            codes=CodeSettings(_env_file=env_file),
            vouchers=VoucherSettings(_env_file=env_file),
            pricing=PricingSettings(_env_file=env_file),
            risk=RiskSettings(_env_file=env_file),
            lifecycle=LifecycleSettings(_env_file=env_file),
            discussion=DiscussionSettings(_env_file=env_file),
            chat=ChatSettings(_env_file=env_file),
            broadcast=BroadcastSettings(_env_file=env_file),
            ui=UiSettings(_env_file=env_file),
        )

    @classmethod
    def environment_variables(cls) -> frozenset[str]:
        """Names of every environment variable the Python code reads."""
        names: set[str] = set()
        for section in cls.model_fields.values():
            section_type = section.annotation
            assert isinstance(section_type, type)
            assert issubclass(section_type, _Section)
            for field in section_type.model_fields.values():
                assert isinstance(field.validation_alias, str)
                names.add(field.validation_alias)
        return frozenset(names)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, loaded once."""
    return Settings.load()
