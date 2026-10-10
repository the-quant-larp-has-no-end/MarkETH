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
from pathlib import Path
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
from app.core.units import PrecisionError, to_micro

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE: Final[Path] = REPO_ROOT / ".env"

INFRA_ONLY_VARIABLES: Final[frozenset[str]] = frozenset(
    {
        "NGINX_HTTP_BIND",
        "NGINX_HTTP_PORT",
        "API_HOST_PORT",
        "WEB_HOST_PORT",
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
        "SCYLLA_SMP",
        "SCYLLA_MEMORY",
        "NGINX_CLIENT_MAX_BODY_SIZE",
        "NGINX_RATE_LIMIT",
        "NGINX_RATE_LIMIT_BURST",
        "NGINX_SSE_READ_TIMEOUT",
        "NGINX_SSE_STREAMS_PER_CALLER",
        "NGINX_CONTENT_SECURITY_POLICY",
        "DEV_USER_ID",
        "DEV_USER_NAME",
        "DEPLOY_SSH_HOST",
        "DEPLOY_PATH",
    }
)
"""Variables in `.env` that only Compose, nginx and the Makefile read, never the Python code."""

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_HEADER_NAME = re.compile(r"[A-Za-z0-9]+(-[A-Za-z0-9]+)*")
_USERNAME = re.compile(USERNAME_PATTERN)

_NOT_IN_A_SLUG = re.compile(r"[^a-z0-9]+")

PRODUCT_ID_MAX_LENGTH: Final[int] = 64
"""Longest id of a product, which is also the width of `vouchers.product_id`."""

PRODUCT_NAME_MAX_LENGTH: Final[int] = 100
"""Longest name of a product, which is also the width of `vouchers.product_name`."""


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


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
CreditMicro = Annotated[int, BeforeValidator(_cr_to_micro), Field(ge=0)]
CreditMicroList = Annotated[list[int], NoDecode, BeforeValidator(_cr_list_to_micro)]
SqlIdentifier = Annotated[str, BeforeValidator(_identifier)]
HeaderName = Annotated[str, BeforeValidator(_header_name)]
PositiveInt = Annotated[int, Field(gt=0)]
VoucherProductList = Annotated[
    tuple[VoucherProduct, ...], NoDecode, BeforeValidator(_products), Field(min_length=1)
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
    sse_heartbeat_seconds: PositiveInt = Field(validation_alias="SSE_HEARTBEAT_SECONDS")

    @property
    def timeout_seconds(self) -> float:
        return self.timeout_ms / 1000


class ScyllaSettings(_Section):
    hosts: CsvList = Field(validation_alias="SCYLLA_HOSTS")
    port: PositiveInt = Field(validation_alias="SCYLLA_PORT")
    keyspace: SqlIdentifier = Field(validation_alias="SCYLLA_KEYSPACE")
    replication_factor: PositiveInt = Field(validation_alias="SCYLLA_REPLICATION_FACTOR")
    request_timeout_ms: PositiveInt = Field(validation_alias="SCYLLA_REQUEST_TIMEOUT_MS")
    outbox_batch_size: PositiveInt = Field(validation_alias="OUTBOX_BATCH_SIZE")
    outbox_poll_interval_ms: PositiveInt = Field(validation_alias="OUTBOX_POLL_INTERVAL_MS")

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
    # The ids of the people with a role, each as `portal_address` reads it: an address
    # at an allowed domain, in lower case. An entry that rule refuses fails loading.
    admin_emails: CsvList = Field(validation_alias="ADMIN_EMAILS")
    barista_emails: CsvList = Field(validation_alias="BARISTA_EMAILS")
    username_min_length: PositiveInt = Field(validation_alias="USERNAME_MIN_LENGTH")
    username_max_length: int = Field(
        validation_alias="USERNAME_MAX_LENGTH", ge=1, le=USERNAME_STORED_MAX_LENGTH
    )
    reserved_usernames: CsvList = Field(validation_alias="RESERVED_USERNAMES")
    upkeep_interval_seconds: PositiveInt = Field(validation_alias="UPKEEP_INTERVAL_SECONDS")

    @field_validator("allowed_email_domains")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @field_validator("admin_emails", "barista_emails")
    @classmethod
    def _ids(cls, value: list[str], info: ValidationInfo) -> list[str]:
        """Read each entry of a role list the way an id of the portal is read.

        An entry that is no address at an allowed domain would give its role to nobody,
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
                    "so nobody would get the role"
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


class GrantSettings(_Section):
    signup_grant_micro: CreditMicro = Field(validation_alias="SIGNUP_GRANT_CR")
    monthly_grant_micro: CreditMicro = Field(validation_alias="MONTHLY_GRANT_CR")


class CodeSettings(_Section):
    length: int = Field(validation_alias="CODE_LENGTH", ge=8, le=32)
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

    @property
    def ttl(self) -> timedelta:
        return timedelta(days=self.expiry_days)

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
    price_display_decimals: int = Field(validation_alias="PRICE_DISPLAY_DECIMALS", ge=0, le=4)

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


class UiSettings(_Section):
    quote_debounce_ms: PositiveInt = Field(validation_alias="QUOTE_DEBOUNCE_MS")
    live_poll_fallback_seconds: PositiveInt = Field(validation_alias="LIVE_POLL_FALLBACK_SECONDS")


class Settings(BaseModel):
    """All settings, grouped the way `.env` is grouped."""

    model_config = ConfigDict(frozen=True)

    app: AppSettings
    postgres: PostgresSettings
    redis: RedisSettings
    scylla: ScyllaSettings
    identity: IdentitySettings
    grants: GrantSettings
    codes: CodeSettings
    vouchers: VoucherSettings
    pricing: PricingSettings
    risk: RiskSettings
    lifecycle: LifecycleSettings
    ui: UiSettings

    @classmethod
    def load(cls, env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
        """Read the process environment, falling back to `env_file` for unset variables."""
        return cls(
            app=AppSettings(_env_file=env_file),
            postgres=PostgresSettings(_env_file=env_file),
            redis=RedisSettings(_env_file=env_file),
            scylla=ScyllaSettings(_env_file=env_file),
            identity=IdentitySettings(_env_file=env_file),
            grants=GrantSettings(_env_file=env_file),
            codes=CodeSettings(_env_file=env_file),
            vouchers=VoucherSettings(_env_file=env_file),
            pricing=PricingSettings(_env_file=env_file),
            risk=RiskSettings(_env_file=env_file),
            lifecycle=LifecycleSettings(_env_file=env_file),
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
