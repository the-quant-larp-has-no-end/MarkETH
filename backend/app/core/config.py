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
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.enums import MarketTier
from app.core.units import PrecisionError, to_micro

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE: Final[Path] = REPO_ROOT / ".env"

INFRA_ONLY_VARIABLES: Final[frozenset[str]] = frozenset(
    {
        "NGINX_HTTP_PORT",
        "API_HOST_PORT",
        "WEB_HOST_PORT",
        "MAILPIT_UI_PORT",
        "SCYLLA_SMP",
        "SCYLLA_MEMORY",
        "NGINX_CLIENT_MAX_BODY_SIZE",
        "NGINX_RATE_LIMIT",
        "NGINX_RATE_LIMIT_BURST",
        "NGINX_SSE_READ_TIMEOUT",
        "NGINX_CONTENT_SECURITY_POLICY",
    }
)
"""Variables in `.env` that only compose.yaml and nginx read, never the Python code."""

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _cr_to_micro(value: object) -> object:
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


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
CreditMicro = Annotated[int, BeforeValidator(_cr_to_micro), Field(ge=0)]
CreditMicroList = Annotated[list[int], NoDecode, BeforeValidator(_cr_list_to_micro)]
SqlIdentifier = Annotated[str, BeforeValidator(_identifier)]
PositiveInt = Annotated[int, Field(gt=0)]


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
    session_cache_ttl_seconds: PositiveInt = Field(validation_alias="SESSION_CACHE_TTL_SECONDS")
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


class MailSettings(_Section):
    host: str = Field(validation_alias="SMTP_HOST")
    port: PositiveInt = Field(validation_alias="SMTP_PORT")
    username: str = Field(validation_alias="SMTP_USERNAME")
    password: str = Field(validation_alias="SMTP_PASSWORD")
    starttls: bool = Field(validation_alias="SMTP_STARTTLS")
    sender: str = Field(validation_alias="MAIL_FROM")


class AuthSettings(_Section):
    allowed_email_domains: CsvList = Field(validation_alias="ALLOWED_EMAIL_DOMAINS")
    canonical_email_domain: str = Field(validation_alias="CANONICAL_EMAIL_DOMAIN")
    admin_emails: CsvList = Field(validation_alias="ADMIN_EMAILS")
    barista_emails: CsvList = Field(validation_alias="BARISTA_EMAILS")
    login_token_bytes: int = Field(validation_alias="LOGIN_TOKEN_BYTES", ge=16)
    login_token_ttl_minutes: PositiveInt = Field(validation_alias="LOGIN_TOKEN_TTL_MINUTES")
    link_limit_per_email: PositiveInt = Field(validation_alias="LOGIN_LINK_LIMIT_PER_EMAIL")
    link_limit_per_email_window_seconds: PositiveInt = Field(
        validation_alias="LOGIN_LINK_LIMIT_PER_EMAIL_WINDOW_SECONDS"
    )
    link_limit_per_ip: PositiveInt = Field(validation_alias="LOGIN_LINK_LIMIT_PER_IP")
    link_limit_per_ip_window_seconds: PositiveInt = Field(
        validation_alias="LOGIN_LINK_LIMIT_PER_IP_WINDOW_SECONDS"
    )
    verify_limit_per_ip: PositiveInt = Field(validation_alias="LOGIN_VERIFY_LIMIT_PER_IP")
    verify_limit_per_ip_window_seconds: PositiveInt = Field(
        validation_alias="LOGIN_VERIFY_LIMIT_PER_IP_WINDOW_SECONDS"
    )
    session_cookie_name: str = Field(validation_alias="SESSION_COOKIE_NAME")
    session_cookie_secure: bool = Field(validation_alias="SESSION_COOKIE_SECURE")
    session_ttl_days: PositiveInt = Field(validation_alias="SESSION_TTL_DAYS")
    session_idle_ttl_days: PositiveInt = Field(validation_alias="SESSION_IDLE_TTL_DAYS")
    session_touch_interval_seconds: PositiveInt = Field(
        validation_alias="SESSION_TOUCH_INTERVAL_SECONDS"
    )

    @field_validator("allowed_email_domains", "admin_emails", "barista_emails")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @field_validator("canonical_email_domain")
    @classmethod
    def _lowercase_domain(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def login_token_ttl(self) -> timedelta:
        return timedelta(minutes=self.login_token_ttl_minutes)

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(days=self.session_ttl_days)

    @property
    def session_idle_ttl(self) -> timedelta:
        return timedelta(days=self.session_idle_ttl_days)

    @property
    def session_touch_interval(self) -> timedelta:
        return timedelta(seconds=self.session_touch_interval_seconds)


class VoucherSettings(_Section):
    signup_grant_micro: CreditMicro = Field(validation_alias="SIGNUP_GRANT_CR")
    monthly_grant_micro: CreditMicro = Field(validation_alias="MONTHLY_GRANT_CR")
    code_min_value_micro: CreditMicro = Field(validation_alias="VOUCHER_CODE_MIN_VALUE_CR")
    code_max_value_micro: CreditMicro = Field(validation_alias="VOUCHER_CODE_MAX_VALUE_CR")
    code_default_value_micro: CreditMicro = Field(validation_alias="VOUCHER_CODE_DEFAULT_VALUE_CR")
    code_length: int = Field(validation_alias="VOUCHER_CODE_LENGTH", ge=8, le=32)
    max_code_issuance_per_month_micro: CreditMicro = Field(
        validation_alias="MAX_CODE_ISSUANCE_PER_MONTH"
    )
    redeem_limit_per_user: PositiveInt = Field(validation_alias="CODE_REDEEM_LIMIT_PER_USER")
    redeem_limit_per_user_window_seconds: PositiveInt = Field(
        validation_alias="CODE_REDEEM_LIMIT_PER_USER_WINDOW_SECONDS"
    )
    redeem_limit_per_ip: PositiveInt = Field(validation_alias="CODE_REDEEM_LIMIT_PER_IP")
    redeem_limit_per_ip_window_seconds: PositiveInt = Field(
        validation_alias="CODE_REDEEM_LIMIT_PER_IP_WINDOW_SECONDS"
    )
    coffee_ticket_value_micro: CreditMicro = Field(validation_alias="COFFEE_TICKET_VALUE_CR")
    coffee_ticket_code_length: int = Field(
        validation_alias="COFFEE_TICKET_CODE_LENGTH", ge=6, le=32
    )
    coffee_ticket_expiry_days: PositiveInt = Field(validation_alias="COFFEE_TICKET_EXPIRY_DAYS")

    @property
    def coffee_ticket_ttl(self) -> timedelta:
        return timedelta(days=self.coffee_ticket_expiry_days)


class PricingSettings(_Section):
    tier_tight_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_TIGHT_B")
    tier_default_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_DEFAULT_B")
    tier_medium_b_micro: CreditMicro = Field(validation_alias="LMSR_TIER_MEDIUM_B")
    # SPEC §4 computes with 50 digits, and this is the lower bound: with 28 to 32
    # digits, quotes at the largest b the spec allows rounded the wrong way (see
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
    mail: MailSettings
    auth: AuthSettings
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
            mail=MailSettings(_env_file=env_file),
            auth=AuthSettings(_env_file=env_file),
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
