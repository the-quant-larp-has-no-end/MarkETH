"""`.env`, `.example.env` and `Settings` describe the same variables (golden rule 7).

Every tunable lives in the environment file and has exactly one typed field in
`Settings`; nothing has a default in code. These tests fail when the three drift apart.
"""

import re
from datetime import timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Final

import pytest
from pydantic_settings import BaseSettings
from sqlalchemy import String

from app.core.config import (
    BADGE_NAME_MAX_LENGTH,
    CODE_MAX_LENGTH,
    DEFAULT_ENV_FILE,
    INFRA_ONLY_VARIABLES,
    MILESTONE_MAX_DAYS,
    PRODUCT_ID_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    REPO_ROOT,
    BadgeLevel,
    Settings,
    StreakMilestone,
    VoucherProduct,
)
from app.core.enums import MarketTier, Outcome
from app.core.identity import USERNAME_PATTERN
from app.core.live import TOPICS_HARD_MAX
from app.core.text import (
    ANSWER_STORED_MAX_LENGTH,
    CHAT_BACKLOG_MAX,
    CHAT_MESSAGE_STORED_MAX_LENGTH,
    QUESTION_STORED_MAX_LENGTH,
)
from app.core.units import MICRO
from app.models import Code, Voucher
from app.pricing import lmsr
from tests.support.settings import TEST_ADMIN_EMAILS, example_streak_settings

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"

_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


def variables_of(env_file: Path) -> list[str]:
    """Names assigned in an env file, in file order, duplicates included."""
    return _ASSIGNMENT.findall(env_file.read_text(encoding="utf-8"))


def value_of(env_file: Path, name: str) -> str:
    """What an env file assigns to `name`, as it is written there."""
    (line,) = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{name}=")
    ]
    return line.removeprefix(f"{name}=")


def test_example_env_assigns_each_variable_once() -> None:
    names = variables_of(EXAMPLE_ENV_FILE)
    assert len(names) == len(set(names)), sorted({n for n in names if names.count(n) > 1})


def test_example_env_holds_exactly_the_settings_and_the_infra_only_variables() -> None:
    documented = set(variables_of(EXAMPLE_ENV_FILE))
    expected = Settings.environment_variables() | INFRA_ONLY_VARIABLES
    assert documented - expected == set(), "in .example.env but read by nothing"
    assert expected - documented == set(), "read by Settings or compose but not in .example.env"


def test_no_variable_is_both_a_setting_and_infra_only() -> None:
    assert Settings.environment_variables() & INFRA_ONLY_VARIABLES == set()


def test_example_env_loads_and_converts_credits_to_micro_units(base_settings: Settings) -> None:
    pricing, risk, codes = base_settings.pricing, base_settings.risk, base_settings.codes
    assert base_settings.grants.signup_grant_micro == 500 * MICRO
    assert base_settings.grants.monthly_grant_micro == 200 * MICRO
    assert pricing.max_stake_per_user_market_micro == 100 * MICRO
    assert (pricing.min_trade_micro, pricing.min_trade_shares_micro) == (MICRO, MICRO)
    assert pricing.stake_presets_micro == [25 * MICRO, 50 * MICRO, 100 * MICRO]
    assert pricing.stake_step_micro == MICRO
    assert [
        pricing.tier_b_micro(tier)
        for tier in (MarketTier.TIGHT, MarketTier.DEFAULT, MarketTier.MEDIUM)
    ] == [500 * MICRO, 1000 * MICRO, 2000 * MICRO]
    assert risk.max_market_budget_micro == 5000 * MICRO
    assert risk.max_aggregate_loss_budget_micro == 70_000 * MICRO
    assert risk.obligations_alert_threshold_micro == 100_000 * MICRO
    assert (codes.min_value_micro, codes.default_value_micro, codes.max_value_micro) == (
        5 * MICRO,
        35 * MICRO,
        500 * MICRO,
    )
    assert codes.max_issuance_per_month_micro == 7000 * MICRO


def test_example_env_lists_the_products_in_the_order_the_wallet_offers_them(
    base_settings: Settings,
) -> None:
    """The name is shown as written; the id is its slug; the price is in µCR."""
    assert [
        (product.id, product.name, product.price_micro)
        for product in base_settings.vouchers.products
    ] == [
        ("coffee", "Coffee", 35 * MICRO),
        ("mate", "Mate", 35 * MICRO),
        ("softdrinks", "Softdrinks", 35 * MICRO),
        ("redbull", "Redbull", 35 * MICRO),
        ("iced-tea", "Iced Tea", 35 * MICRO),
        ("tea", "Tea", 30 * MICRO),
        ("twix", "Twix", 15 * MICRO),
        ("snickers", "Snickers", 15 * MICRO),
        ("m-ms", "M&Ms", 15 * MICRO),
        ("chewing-gum", "Chewing Gum", 20 * MICRO),
        ("toblerone", "Toblerone", 5 * MICRO),
    ]


def test_a_product_is_found_by_its_id_and_the_cheapest_one_sets_what_a_voucher_needs(
    base_settings: Settings,
) -> None:
    vouchers = base_settings.vouchers
    assert vouchers.product("iced-tea") == VoucherProduct("iced-tea", "Iced Tea", 35 * MICRO)
    assert vouchers.product("m-ms") == VoucherProduct("m-ms", "M&Ms", 15 * MICRO)
    for no_id in ("Iced Tea", "ICED-TEA", "iced_tea", "", "mate "):
        assert vouchers.product(no_id) is None, no_id
    assert vouchers.lowest_price_micro == 5 * MICRO


def column_width(column: str) -> int | None:
    """The length of a VARCHAR column of `vouchers`."""
    column_type = Voucher.__table__.columns[column].type
    assert isinstance(column_type, String)
    return column_type.length


def test_every_product_fits_the_columns_a_voucher_keeps_it_in(base_settings: Settings) -> None:
    """A voucher row copies the id and the name of its product (SPEC §7)."""
    assert column_width("product_id") == PRODUCT_ID_MAX_LENGTH == 64
    assert column_width("product_name") == PRODUCT_NAME_MAX_LENGTH == 100
    for product in base_settings.vouchers.products:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", product.id), product
        assert len(product.id) <= PRODUCT_ID_MAX_LENGTH
        assert 0 < len(product.name) <= PRODUCT_NAME_MAX_LENGTH
        assert product.price_micro > 0


def test_example_env_lists_and_derived_values(base_settings: Settings) -> None:
    assert base_settings.identity.allowed_email_domains == ["ethz.ch"]
    assert base_settings.identity.header_user_id == "X-User-Id"
    assert base_settings.identity.header_user_name == "X-User-Name"
    assert base_settings.identity.upkeep_interval == timedelta(seconds=60)
    assert (
        base_settings.identity.username_min_length,
        base_settings.identity.username_max_length,
    ) == (3, 20)
    assert base_settings.app.timezone.key == "Europe/Zurich"
    assert base_settings.postgres.lock_timeout_ms == 5000
    assert base_settings.redis.timeout_seconds == 0.25
    assert base_settings.scylla.hosts == ["localhost"]


def test_the_tests_run_with_two_bootstrap_admins_where_the_example_names_one(
    base_settings: Settings,
) -> None:
    """An admin who traded a market may not settle it (A.4), and no admin acts on their
    own account, so a test of either rule needs a second admin. The list of bootstrap
    admins, the environment's name and what a streak books are the only things
    `base_settings` does not take from `.example.env`."""
    example = Settings.load(EXAMPLE_ENV_FILE)
    identity = base_settings.identity

    assert identity.admin_emails == list(TEST_ADMIN_EMAILS)
    assert len(set(identity.admin_emails)) == 2
    assert len(example.identity.admin_emails) == 1, "the example keeps one admin"

    changed = {"admin_emails"}
    assert identity.model_dump(exclude=changed) == example.identity.model_dump(exclude=changed)
    assert base_settings.model_dump(exclude={"identity", "app", "streaks"}) == example.model_dump(
        exclude={"identity", "app", "streaks"}
    )
    assert base_settings.app.model_dump(exclude={"env"}) == example.app.model_dump(exclude={"env"})
    assert base_settings.app.env == "test"


def test_the_tests_run_with_a_streak_that_books_nothing_unless_a_test_asks_for_the_example(
    base_settings: Settings,
) -> None:
    """A trade counts a day of a streak. With nothing booked for it, a test of a trade
    reads the credits of the trade alone; the marker `streak_rewards` gives a test the
    values of `.example.env` (tests/conftest.py, `container_settings`)."""
    example = Settings.load(EXAMPLE_ENV_FILE).streaks

    assert (base_settings.streaks.daily_reward_micro, base_settings.streaks.milestones) == (0, ())
    assert base_settings.streaks.lock_timeout_ms == example.lock_timeout_ms
    assert base_settings.streaks.milestone(7) is None
    assert example_streak_settings() == example
    assert example.daily_reward_micro == 2 * MICRO
    assert [milestone.days for milestone in example.milestones] == [7, 30, 100]


def test_the_example_reserves_the_usernames_that_would_pass_for_an_official_account(
    base_settings: Settings,
) -> None:
    identity = base_settings.identity
    assert identity.reserved_usernames == [
        "admin",
        "administrator",
        "vis",
        "viscon",
        "vseth",
        "eth",
        "ethz",
        "marketh",
        "moderator",
        "support",
        "staff",
        "barista",
        "system",
        "official",
    ]
    assert len(set(identity.reserved_usernames)) == len(identity.reserved_usernames)
    for name in identity.reserved_usernames:
        assert re.fullmatch(USERNAME_PATTERN, name), name


def test_the_example_publishes_the_stack_on_this_machine_alone() -> None:
    """The development proxy names a caller to whoever reaches it, so the stack of a
    laptop is not offered to the network it is on. Both are read by Compose alone."""
    assert value_of(EXAMPLE_ENV_FILE, "NGINX_HTTP_BIND") == "127.0.0.1"
    assert int(value_of(EXAMPLE_ENV_FILE, "API_SHUTDOWN_TIMEOUT_SECONDS")) == 5
    assert {"NGINX_HTTP_BIND", "API_SHUTDOWN_TIMEOUT_SECONDS"} <= INFRA_ONLY_VARIABLES


def test_the_example_names_the_network_between_the_proxy_and_the_web_app() -> None:
    """Compose gives that network this range, and nginx tells the web app from everybody
    else by Docker's own address on it, the first of the range. Neither is read by the
    Python code."""
    subnet = ip_network(value_of(EXAMPLE_ENV_FILE, "EDGE_SUBNET"))
    gateway = ip_address(value_of(EXAMPLE_ENV_FILE, "EDGE_GATEWAY"))

    assert (str(subnet), str(gateway)) == ("192.168.209.0/28", "192.168.209.1")
    assert gateway == next(subnet.hosts())
    assert subnet.is_private
    assert {"EDGE_SUBNET", "EDGE_GATEWAY"} <= INFRA_ONLY_VARIABLES


def test_example_env_sets_the_waits_the_chart_bounds_and_the_most_an_adjustment_adds(
    base_settings: Settings,
) -> None:
    assert base_settings.postgres.pool_timeout_seconds == 10
    assert base_settings.vouchers.rescan_seconds == 120
    assert base_settings.vouchers.rescan_window == timedelta(minutes=2)
    assert base_settings.scylla.history_max_points == 500
    assert base_settings.scylla.history_read_limit == 5000
    assert base_settings.risk.max_admin_adjustment_micro == 1000 * MICRO


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"POSTGRES_POOL_TIMEOUT_SECONDS": "0"}, "POSTGRES_POOL_TIMEOUT_SECONDS"),
        ({"POSTGRES_POOL_TIMEOUT_SECONDS": "10s"}, "POSTGRES_POOL_TIMEOUT_SECONDS"),
        ({"VOUCHER_RESCAN_SECONDS": "0"}, "VOUCHER_RESCAN_SECONDS"),
        ({"VOUCHER_RESCAN_SECONDS": "-120"}, "VOUCHER_RESCAN_SECONDS"),
        ({"HISTORY_MAX_POINTS": "1"}, "HISTORY_MAX_POINTS"),
        ({"HISTORY_READ_LIMIT": "0"}, "HISTORY_READ_LIMIT"),
        (
            {"HISTORY_READ_LIMIT": "499"},
            "HISTORY_READ_LIMIT must not be below HISTORY_MAX_POINTS",
        ),
        (
            {"HISTORY_MAX_POINTS": "5001"},
            "HISTORY_READ_LIMIT must not be below HISTORY_MAX_POINTS",
        ),
        ({"MAX_ADMIN_ADJUSTMENT_CR": "-1"}, "MAX_ADMIN_ADJUSTMENT_CR"),
        ({"MAX_ADMIN_ADJUSTMENT_CR": "plenty"}, "MAX_ADMIN_ADJUSTMENT_CR"),
        ({"MAX_ADMIN_ADJUSTMENT_CR": "0.0000001"}, "MAX_ADMIN_ADJUSTMENT_CR"),
    ],
    ids=[
        "no-wait-for-the-pool",
        "pool-timeout-with-a-unit",
        "no-rescan-window",
        "a-negative-rescan-window",
        "a-chart-of-one-point",
        "nothing-read-for-a-chart",
        "fewer-points-read-than-answered",
        "more-points-answered-than-read",
        "a-negative-adjustment-cap",
        "an-adjustment-cap-in-words",
        "an-adjustment-cap-finer-than-a-micro-credit",
    ],
)
def test_a_wait_a_chart_bound_or_an_adjustment_cap_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    with pytest.raises(ValueError, match=named):
        Settings.load(_example_with(tmp_path, monkeypatch, **values))


def test_a_chart_may_be_as_small_as_its_two_ends_and_an_adjustment_may_add_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first and the latest point are always kept, so two is the least a chart
    answers; reading exactly as many as are answered thins nothing. A cap of zero
    leaves adjustments that take away."""
    env_file = _example_with(
        tmp_path,
        monkeypatch,
        HISTORY_MAX_POINTS="2",
        HISTORY_READ_LIMIT="2",
        MAX_ADMIN_ADJUSTMENT_CR="0",
        POSTGRES_POOL_TIMEOUT_SECONDS="1",
        VOUCHER_RESCAN_SECONDS="1",
    )

    loaded = Settings.load(env_file)

    assert (loaded.scylla.history_max_points, loaded.scylla.history_read_limit) == (2, 2)
    assert loaded.risk.max_admin_adjustment_micro == 0
    assert loaded.postgres.pool_timeout_seconds == 1
    assert loaded.vouchers.rescan_window == timedelta(seconds=1)


def test_public_base_url_is_an_allowed_origin(base_settings: Settings) -> None:
    """A browser on the app's own origin must pass the Origin check (SPEC §3)."""
    assert base_settings.app.public_base_url in base_settings.app.allowed_origins


def test_a_missing_variable_fails_loading_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAX_OPEN_MARKETS", raising=False)
    incomplete = tmp_path / ".env"
    incomplete.write_text(
        "\n".join(
            line
            for line in EXAMPLE_ENV_FILE.read_text(encoding="utf-8").splitlines()
            if not line.startswith("MAX_OPEN_MARKETS=")
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MAX_OPEN_MARKETS"):
        Settings.load(incomplete)


@pytest.mark.parametrize("precision", ["28", "45", "49"])
def test_fewer_digits_than_spec_section_4_names_fail_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, precision: str
) -> None:
    """Fewer than 50 digits round against the market maker at the largest b (lmsr.py)."""
    monkeypatch.delenv("PRICING_DECIMAL_PRECISION", raising=False)
    lowered = tmp_path / ".env"
    lowered.write_text(
        EXAMPLE_ENV_FILE.read_text(encoding="utf-8").replace(
            "PRICING_DECIMAL_PRECISION=50", f"PRICING_DECIMAL_PRECISION={precision}"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="PRICING_DECIMAL_PRECISION"):
        Settings.load(lowered)


def test_more_digits_than_the_default_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRICING_DECIMAL_PRECISION", raising=False)
    raised = tmp_path / ".env"
    raised.write_text(
        EXAMPLE_ENV_FILE.read_text(encoding="utf-8").replace(
            "PRICING_DECIMAL_PRECISION=50", "PRICING_DECIMAL_PRECISION=60"
        ),
        encoding="utf-8",
    )
    assert Settings.load(raised).pricing.decimal_precision == 60


def _example_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **values: str) -> Path:
    """A copy of `.example.env` in which each of `values` replaces the line of its variable."""
    lines = EXAMPLE_ENV_FILE.read_text(encoding="utf-8").splitlines()
    for name, value in values.items():
        monkeypatch.delenv(name, raising=False)
        (index,) = [i for i, line in enumerate(lines) if line.startswith(f"{name}=")]
        lines[index] = f"{name}={value}"
    changed = tmp_path / ".env"
    changed.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"USERNAME_MIN_LENGTH": "21"}, "USERNAME_MIN_LENGTH must not be above"),
        ({"USERNAME_MAX_LENGTH": "33"}, "USERNAME_MAX_LENGTH"),
        ({"USERNAME_MIN_LENGTH": "0"}, "USERNAME_MIN_LENGTH"),
        ({"ALLOWED_EMAIL_DOMAINS": ""}, "ALLOWED_EMAIL_DOMAINS"),
        ({"RESERVED_USERNAMES": "admin,Staff"}, "RESERVED_USERNAMES"),
        ({"RESERVED_USERNAMES": "admin,vis con"}, "RESERVED_USERNAMES"),
        ({"RESERVED_USERNAMES": "x" * 33}, "RESERVED_USERNAMES"),
        ({"IDENTITY_HEADER_USER_ID": "X_User_Id"}, "IDENTITY_HEADER_USER_ID"),
        ({"IDENTITY_HEADER_USER_NAME": "X-User-Name:"}, "IDENTITY_HEADER_USER_NAME"),
        ({"IDENTITY_HEADER_USER_ID": ""}, "IDENTITY_HEADER_USER_ID"),
        ({"UPKEEP_INTERVAL_SECONDS": "0"}, "UPKEEP_INTERVAL_SECONDS"),
    ],
    ids=[
        "username-lengths-out-of-order",
        "username-longer-than-the-column",
        "username-of-no-length",
        "nobody-allowed",
        "reserved-name-with-a-capital",
        "reserved-name-with-a-space",
        "reserved-name-of-33-characters",
        "header-name-with-underscores",
        "header-name-with-a-colon",
        "header-without-a-name",
        "upkeep-without-an-interval",
    ],
)
def test_an_identity_setting_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    with pytest.raises(ValueError, match=named):
        Settings.load(_example_with(tmp_path, monkeypatch, **values))


@pytest.mark.parametrize(
    ("written", "products"),
    [
        ("Mate:35", [("mate", "Mate", 35 * MICRO)]),
        (
            '"Iced Tea:3.5,M&Ms:0.000001"',
            [("iced-tea", "Iced Tea", 3_500_000), ("m-ms", "M&Ms", 1)],
        ),
        ('" Tea : 30 ,, Twix:15 ,"', [("tea", "Tea", 30 * MICRO), ("twix", "Twix", 15 * MICRO)]),
        ('"Caf\u00e9 cr\u00e8me:4"', [("cafe-creme", "Caf\u00e9 cr\u00e8me", 4 * MICRO)]),
        ('"Club-Mate (0.5 l):35"', [("club-mate-0-5-l", "Club-Mate (0.5 l)", 35 * MICRO)]),
        ('"Tea at 16:30:30"', [("tea-at-16-30", "Tea at 16:30", 30 * MICRO)]),
        ('"7up:20,UP 7:20"', [("7up", "7up", 20 * MICRO), ("up-7", "UP 7", 20 * MICRO)]),
    ],
    ids=[
        "one-product",
        "a-space-an-ampersand-and-the-smallest-price",
        "blanks-around-the-items",
        "letters-outside-ascii",
        "punctuation",
        "a-colon-in-the-name",
        "ids-that-differ",
    ],
)
def test_products_are_names_with_prices_and_the_slug_of_the_name_is_the_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    written: str,
    products: list[tuple[str, str, int]],
) -> None:
    env_file = _example_with(tmp_path, monkeypatch, VOUCHER_PRODUCTS=written)

    loaded = Settings.load(env_file).vouchers.products

    assert [(product.id, product.name, product.price_micro) for product in loaded] == products


@pytest.mark.parametrize(
    ("written", "reason"),
    [
        ('""', "at least 1 item"),
        ('","', "at least 1 item"),
        ('"Tea"', "expected Name:price"),
        ('":30"', "expected Name:price"),
        ('"Tea:"', "not a valid CR amount"),
        ('"Tea:free"', "not a valid CR amount"),
        ('"Tea:30 CR"', "not a valid CR amount"),
        ('"Tea:0.0000001"', "not a valid CR amount"),
        ('"Tea:0"', "must be above zero"),
        ('"Tea:-30"', "must be above zero"),
        ('"Tea:30,tea:35"', "has the id of another product"),
        ('"Iced Tea:30,Iced-Tea:35"', "has the id of another product"),
        ('"!?:30"', "no letter or digit"),
        (f'"{"x" * 101}:30"', "is longer than 100"),
        (f'"{"a " * 32}a:30"', "is longer than 64"),
    ],
    ids=[
        "no-product",
        "only-a-comma",
        "no-price",
        "no-name",
        "an-empty-price",
        "a-price-in-words",
        "a-price-with-its-unit",
        "a-seventh-decimal-place",
        "a-price-of-nothing",
        "a-negative-price",
        "one-name-in-two-cases",
        "one-name-with-other-punctuation",
        "a-name-without-letters",
        "a-name-of-101-characters",
        "an-id-of-65-characters",
    ],
)
def test_a_product_list_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: str, reason: str
) -> None:
    with pytest.raises(ValueError, match="VOUCHER_PRODUCTS") as refused:
        Settings.load(_example_with(tmp_path, monkeypatch, VOUCHER_PRODUCTS=written))
    assert reason in str(refused.value)


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"CODE_DEFAULT_VALUE_CR": "4.999999"}, "CODE_DEFAULT_VALUE_CR must lie between"),
        ({"CODE_DEFAULT_VALUE_CR": "500.000001"}, "CODE_DEFAULT_VALUE_CR must lie between"),
        ({"CODE_MIN_VALUE_CR": "501"}, "CODE_DEFAULT_VALUE_CR must lie between"),
        ({"CODE_MAX_VALUE_CR": "4"}, "CODE_DEFAULT_VALUE_CR must lie between"),
        ({"CODE_MIN_VALUE_CR": "0"}, "CODE_MIN_VALUE_CR must be above zero"),
        ({"POSTGRES_LOCK_TIMEOUT_MS": "0"}, "POSTGRES_LOCK_TIMEOUT_MS"),
        ({"POSTGRES_LOCK_TIMEOUT_MS": "5s"}, "POSTGRES_LOCK_TIMEOUT_MS"),
    ],
    ids=[
        "code-default-below-the-minimum",
        "code-default-above-the-maximum",
        "code-minimum-above-the-maximum",
        "code-maximum-below-the-minimum",
        "a-code-worth-nothing",
        "no-lock-timeout",
        "lock-timeout-with-a-unit",
    ],
)
def test_a_code_value_or_a_lock_timeout_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    with pytest.raises(ValueError, match=named):
        Settings.load(_example_with(tmp_path, monkeypatch, **values))


@pytest.mark.parametrize(
    "written",
    ["0", "0.5", "1.5", "2.000001", "-1", "one", ""],
    ids=[
        "no-step",
        "half-a-credit",
        "one-and-a-half",
        "a-micro-credit-above-two",
        "a-negative-step",
        "a-word",
        "nothing",
    ],
)
def test_a_stake_step_that_is_no_whole_number_of_credits_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: str
) -> None:
    """The slider of the trade sheet counts in whole credits, so its step is one or
    more of them: `MarketViewer.buy_slider` carries it as an integer."""
    with pytest.raises(ValueError, match="TRADE_STAKE_STEP_CR"):
        Settings.load(_example_with(tmp_path, monkeypatch, TRADE_STAKE_STEP_CR=written))


@pytest.mark.parametrize(("written", "credits"), [("1", 1), ("5", 5), ("25.000000", 25)])
def test_a_stake_step_of_whole_credits_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: str, credits: int
) -> None:
    env_file = _example_with(tmp_path, monkeypatch, TRADE_STAKE_STEP_CR=written)

    assert Settings.load(env_file).pricing.stake_step_micro == credits * MICRO


def test_the_longest_code_the_settings_allow_fits_the_column_that_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_settings: Settings
) -> None:
    """A code is kept in `codes.code` while it can be redeemed, so CODE_LENGTH may not
    ask for more characters than that column holds."""
    kept_in = Code.__table__.columns["code"].type
    assert isinstance(kept_in, String)
    assert kept_in.length == CODE_MAX_LENGTH == 32
    assert base_settings.codes.length <= CODE_MAX_LENGTH

    longest = _example_with(tmp_path, monkeypatch, CODE_LENGTH=str(CODE_MAX_LENGTH))
    assert Settings.load(longest).codes.length == CODE_MAX_LENGTH
    with pytest.raises(ValueError, match="CODE_LENGTH"):
        Settings.load(_example_with(tmp_path, monkeypatch, CODE_LENGTH=str(CODE_MAX_LENGTH + 1)))


def test_code_values_may_all_be_the_same(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MIN <= DEFAULT <= MAX: every code is then worth exactly that."""
    env_file = _example_with(
        tmp_path,
        monkeypatch,
        CODE_MIN_VALUE_CR="35",
        CODE_DEFAULT_VALUE_CR="35",
        CODE_MAX_VALUE_CR="35",
    )

    codes = Settings.load(env_file).codes

    assert codes.min_value_micro == codes.default_value_micro == codes.max_value_micro


def test_the_largest_market_budget_is_priceable_at_the_configured_precision(
    base_settings: Settings,
) -> None:
    """An admin may give a CUSTOM market any budget up to MAX_MARKET_BUDGET, and the
    database keeps a market's b while it is OPEN. `lmsr` refuses to quote a b that
    PRICING_DECIMAL_PRECISION digits cannot round reliably, so the b of the largest
    budget has to be one it quotes: every quote and trade on such a market would fail
    otherwise."""
    pricing = base_settings.pricing
    precision = pricing.decimal_precision
    budget_micro = base_settings.risk.max_market_budget_micro
    b_micro = lmsr.b_from_budget_micro(budget_micro, precision=precision)
    largest = lmsr.MarketState(q_yes=0, q_no=0, b=b_micro)
    stake_micro = pricing.max_stake_per_user_market_micro

    by_budget = lmsr.quote_buy_for_budget(largest, Outcome.YES, stake_micro, precision=precision)
    by_shares = lmsr.quote_buy_shares(
        largest, Outcome.NO, pricing.min_trade_shares_micro, precision=precision
    )
    sold = lmsr.quote_sell_shares(
        by_budget.after, Outcome.YES, by_budget.shares_micro, precision=precision
    )

    assert lmsr.max_loss_micro(b_micro, precision=precision) == budget_micro
    assert by_budget.amount_micro == stake_micro
    assert 0 < by_shares.amount_micro <= pricing.min_trade_shares_micro
    assert sold.after == largest
    assert 0 <= by_budget.amount_micro - sold.amount_micro <= 1


def test_domains_are_read_in_lower_case_and_bootstrap_admins_the_way_an_id_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain has one spelling, and so has an id: `ADMIN_EMAILS` holds the ids of its
    people as the portal's id is read, in lower case and at the domain written."""
    env_file = _example_with(
        tmp_path,
        monkeypatch,
        ALLOWED_EMAIL_DOMAINS="Student.ETHZ.ch, ethz.CH",
        ADMIN_EMAILS="Ada@ETHZ.ch, grace@student.ethz.ch",
    )

    identity = Settings.load(env_file).identity

    assert identity.allowed_email_domains == ["student.ethz.ch", "ethz.ch"]
    assert identity.admin_emails == ["ada@ethz.ch", "grace@student.ethz.ch"]


@pytest.mark.parametrize(
    "entry",
    [
        "someone@uzh.ch",
        "someone@student.ethz.ch",
        "not-an-address",
        "two@@ethz.ch",
        "a b@ethz.ch",
        "someone+vis@ethz.ch",
        "@ethz.ch",
        "someone@",
        "s" + chr(0xF6) + "meone@ethz.ch",
        f"{'a' * 65}@ethz.ch",
    ],
    ids=[
        "foreign-domain",
        "domain-that-is-not-allowed",
        "no-address",
        "two-at-signs",
        "space-inside",
        "plus-tag",
        "no-local-part",
        "no-domain",
        "letter-outside-ascii",
        "local-part-of-65-characters",
    ],
)
def test_a_bootstrap_admin_that_is_no_id_fails_loading_and_the_error_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """An entry the id rule refuses would make nobody an admin: a typing mistake in
    `.env` that shows as a missing admin. The process does not start on it."""
    env_file = _example_with(tmp_path, monkeypatch, ADMIN_EMAILS=f"admin@ethz.ch,{entry}")

    with pytest.raises(ValueError, match="ADMIN_EMAILS") as refused:
        Settings.load(env_file)

    message = str(refused.value)
    assert "1 validation error" in message
    assert repr(entry) in message
    assert "is not an address at ethz.ch" in message


def test_the_only_people_the_settings_name_are_the_bootstrap_admins() -> None:
    """Every other role is one an admin gave to an account: a barista is a row, not an
    entry of `.env`."""
    lists_of_people = {name for name in Settings.environment_variables() if "EMAILS" in name}

    assert lists_of_people == {"ADMIN_EMAILS"}


def test_the_list_of_bootstrap_admins_may_be_empty_and_a_reserved_list_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is an admin by configuration where `.env` names nobody. The settings
    load; who can then be made an admin is not theirs to say."""
    env_file = _example_with(tmp_path, monkeypatch, ADMIN_EMAILS="", RESERVED_USERNAMES="")

    identity = Settings.load(env_file).identity

    assert (identity.admin_emails, identity.reserved_usernames) == ([], [])


def test_local_env_has_the_same_keys_as_the_example() -> None:
    """`.env` is gitignored; where a developer has one, it must not have drifted."""
    if not DEFAULT_ENV_FILE.exists():
        pytest.skip("no local .env")
    assert set(variables_of(DEFAULT_ENV_FILE)) == set(variables_of(EXAMPLE_ENV_FILE))


# --- The settings of the features round ----------------------------------------------

LEFT_OUT: Final[frozenset[str]] = frozenset({"LIVE_REPLAY_EVENTS", "LEADERBOARD_SNAPSHOT_VIEWS"})
"""Two variables a design names that are no settings: nothing keeps an event for a
stream that comes back, and nothing keeps a ranking."""

INFRA_ONLY_OF_THE_ROUND: Final[dict[str, str]] = {
    "API_NOFILE_LIMIT": "4096",
    "NGINX_NOFILE_LIMIT": "8192",
    "MEDIA_MEMORY_LIMIT": "512m",
    "MEDIA_CPUS": "1.5",
    "MEDIA_PIDS_LIMIT": "128",
    "MEDIA_HLS_TMPFS_SIZE": "64m",
    "MEDIA_FFMPEG_THREADS": "2",
    "MEDIA_X264_PRESET": "veryfast",
    "NGINX_BROADCAST_UPLOADS_PER_CALLER": "2",
}
"""What only Compose, nginx and the media box read, with the value the example gives it."""


def test_the_example_sets_what_only_the_stack_reads_and_leaves_out_what_is_no_setting() -> None:
    for name, value in INFRA_ONLY_OF_THE_ROUND.items():
        assert name in INFRA_ONLY_VARIABLES, name
        assert value_of(EXAMPLE_ENV_FILE, name) == value, name
    assert value_of(EXAMPLE_ENV_FILE, "NGINX_SSE_STREAMS_PER_CALLER") == "6"

    documented = set(variables_of(EXAMPLE_ENV_FILE))
    assert documented & LEFT_OUT == set()
    assert (Settings.environment_variables() | INFRA_ONLY_VARIABLES) & LEFT_OUT == set()


def test_the_pool_keeps_its_connections_and_opens_none_on_top(base_settings: Settings) -> None:
    """A full room's reads are served from kept connections."""
    assert (base_settings.postgres.pool_size, base_settings.postgres.max_overflow) == (30, 0)


def test_the_heartbeat_is_a_setting_of_the_event_stream_and_of_nothing_else(
    base_settings: Settings,
) -> None:
    assert base_settings.live.heartbeat_seconds == 15
    assert "sse_heartbeat_seconds" not in type(base_settings.redis).model_fields
    assert not hasattr(base_settings.redis, "sse_heartbeat_seconds")


def test_the_example_sets_the_event_stream_the_cooldown_and_the_browser_paces(
    base_settings: Settings,
) -> None:
    live, ui = base_settings.live, base_settings.ui

    assert live.model_dump() == {
        "heartbeat_seconds": 15,
        "max_streams": 200,
        "stream_max_seconds": 900,
        "retry_ms": 2000,
        "topics_per_stream": 8,
        "queue_events": 256,
        "event_max_bytes": 4096,
        "leaderboard_refresh_seconds": 5,
    }
    assert base_settings.identity.programme_change_cooldown_days == 30
    assert base_settings.identity.programme_change_cooldown == timedelta(days=30)
    assert (
        ui.live_poll_fallback_seconds,
        ui.live_announce_spread_ms,
        ui.live_viewer_refresh_seconds,
    ) == (10, 1000, 5)


def test_the_example_sets_streaks_badges_and_the_shortcuts_of_a_buy() -> None:
    example = Settings.load(EXAMPLE_ENV_FILE)
    streaks, badges, pricing = example.streaks, example.badges, example.pricing

    assert streaks.daily_reward_micro == 2 * MICRO
    assert streaks.milestones == (
        StreakMilestone(days=7, bonus_micro=10 * MICRO),
        StreakMilestone(days=30, bonus_micro=50 * MICRO),
        StreakMilestone(days=100, bonus_micro=200 * MICRO),
    )
    assert streaks.lock_timeout_ms == 250
    assert streaks.milestone(30) == StreakMilestone(days=30, bonus_micro=50 * MICRO)
    assert streaks.milestone(29) is None

    assert badges.levels == (
        BadgeLevel(level=1, name="Rookie", wins=0),
        BadgeLevel(level=2, name="Forecaster", wins=1),
        BadgeLevel(level=3, name="Analyst", wins=5),
        BadgeLevel(level=4, name="Strategist", wins=15),
        BadgeLevel(level=5, name="Oracle", wins=40),
    )
    assert [badges.level_for(wins).name for wins in (0, 1, 4, 5, 14, 15, 39, 40, 10_000)] == [
        "Rookie",
        "Forecaster",
        "Forecaster",
        "Analyst",
        "Analyst",
        "Strategist",
        "Strategist",
        "Oracle",
        "Oracle",
    ]
    assert badges.next_after(badges.levels[0]) == badges.levels[1]
    assert badges.next_after(badges.levels[4]) is None

    assert pricing.share_presets_micro == [10 * MICRO, 50 * MICRO, 100 * MICRO]
    assert pricing.share_step_micro == MICRO
    # The stake shortcuts stay until nothing reads them.
    assert pricing.stake_presets_micro == [25 * MICRO, 50 * MICRO, 100 * MICRO]
    assert pricing.stake_step_micro == MICRO


def test_the_example_sets_discussion_chat_and_live_video(base_settings: Settings) -> None:
    assert base_settings.discussion.model_dump() == {
        "question_min_length": 10,
        "question_max_length": 300,
        "answer_max_length": 1000,
        "max_questions_per_market": 100,
        "max_waiting_per_user": 3,
        "question_limit_per_user": 5,
        "question_limit_per_user_window_seconds": 600,
    }
    assert base_settings.chat.model_dump() == {
        "message_max_length": 200,
        "message_limit_per_user": 5,
        "message_limit_per_user_window_seconds": 10,
        "room_message_limit": 20,
        "room_message_limit_window_seconds": 10,
        "backlog_size": 100,
    }
    assert base_settings.broadcast.model_dump() == {
        "max_live": 1,
        "requests_per_user_per_day": 3,
        "max_minutes": 120,
        "stall_seconds": 120,
        "max_viewers": 100,
        "access_cache_seconds": 5,
        "chunk_ms": 1000,
        "chunk_max_bytes": 1_048_576,
        "upload_backlog_seconds": 15,
        "upload_timeout_ms": 10_000,
        "upload_retry_ms": 1000,
        "restart_window_ms": 30_000,
        "wait_notice_ms": 4000,
        "player_retry_ms": 2000,
        "capture_video_kbps": 1800,
        "input_max_width": 1920,
        "input_max_height": 1080,
        "video_height": 720,
        "video_fps": 24,
        "video_kbps": 1200,
        "audio_kbps": 64,
        "hls_segment_seconds": 2,
        "hls_playlist_segments": 6,
        "media_socket": "/run/marketh-media/box.sock",
        "media_timeout_ms": 2000,
    }


def test_a_field_of_the_three_sections_is_its_variable_without_the_prefix() -> None:
    for section, prefix in (
        ("discussion", "DISCUSSION_"),
        ("chat", "CHAT_"),
        ("broadcast", "BROADCAST_"),
    ):
        section_type = Settings.model_fields[section].annotation
        assert isinstance(section_type, type)
        assert issubclass(section_type, BaseSettings)
        for name, field in section_type.model_fields.items():
            assert field.validation_alias == f"{prefix}{name.upper()}", (section, name)


def test_live_video_works_three_values_out_of_its_settings(base_settings: Settings) -> None:
    broadcast = base_settings.broadcast

    # A quarter of BROADCAST_STALL_SECONDS.
    assert broadcast.heartbeat_interval == timedelta(seconds=30)
    # BROADCAST_ACCESS_CACHE_SECONDS and the six segments of two seconds a playlist names.
    assert broadcast.seat_seconds == 5 + 2 * 6
    # Fifteen seconds at 1800 + 64 kbit/s.
    assert broadcast.upload_backlog_bytes == 15 * (1800 + 64) * 125 == 3_495_000


def _breaches(*cases: tuple[str, dict[str, str], str]) -> pytest.MarkDecorator:
    """Parametrises a test over `(id, values, named)`: with `values` in place of the
    example's lines, loading fails and the error says `named`."""
    return pytest.mark.parametrize(
        ("values", "named"),
        [pytest.param(values, named, id=case_id) for case_id, values, named in cases],
    )


def _refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(named)) as refused:
        Settings.load(_example_with(tmp_path, monkeypatch, **values))
    # Every breach names the variable that breaks its rule.
    assert any(name in str(refused.value) for name in values), str(refused.value)


@_breaches(
    ("no-heartbeat", {"SSE_HEARTBEAT_SECONDS": "0"}, "SSE_HEARTBEAT_SECONDS"),
    ("half-a-heartbeat", {"SSE_HEARTBEAT_SECONDS": "1.5"}, "SSE_HEARTBEAT_SECONDS"),
    ("no-stream", {"LIVE_MAX_STREAMS": "0"}, "LIVE_MAX_STREAMS"),
    ("streams-in-words", {"LIVE_MAX_STREAMS": "many"}, "LIVE_MAX_STREAMS"),
    ("a-stream-of-no-length", {"LIVE_STREAM_MAX_SECONDS": "0"}, "LIVE_STREAM_MAX_SECONDS"),
    (
        "a-stream-as-long-as-two-beats",
        {"LIVE_STREAM_MAX_SECONDS": "30"},
        "LIVE_STREAM_MAX_SECONDS must be above twice SSE_HEARTBEAT_SECONDS",
    ),
    (
        "beats-half-as-long-as-a-stream",
        {"SSE_HEARTBEAT_SECONDS": "450"},
        "LIVE_STREAM_MAX_SECONDS must be above twice SSE_HEARTBEAT_SECONDS",
    ),
    ("no-wait-before-a-retry", {"LIVE_RETRY_MS": "0"}, "LIVE_RETRY_MS"),
    ("no-topic", {"LIVE_TOPICS_PER_STREAM": "0"}, "LIVE_TOPICS_PER_STREAM"),
    (
        "more-topics-than-a-request-takes",
        {"LIVE_TOPICS_PER_STREAM": str(TOPICS_HARD_MAX + 1)},
        "LIVE_TOPICS_PER_STREAM",
    ),
    ("no-queue", {"LIVE_QUEUE_EVENTS": "0"}, "LIVE_QUEUE_EVENTS"),
    ("an-event-of-511-bytes", {"LIVE_EVENT_MAX_BYTES": "511"}, "LIVE_EVENT_MAX_BYTES"),
    (
        "no-leaderboard-interval",
        {"LEADERBOARD_REFRESH_SECONDS": "0"},
        "LEADERBOARD_REFRESH_SECONDS",
    ),
    (
        "a-negative-cooldown",
        {"PROGRAMME_CHANGE_COOLDOWN_DAYS": "-1"},
        "PROGRAMME_CHANGE_COOLDOWN_DAYS",
    ),
    (
        "a-cooldown-above-a-year",
        {"PROGRAMME_CHANGE_COOLDOWN_DAYS": "366"},
        "PROGRAMME_CHANGE_COOLDOWN_DAYS",
    ),
    (
        "half-a-day-of-cooldown",
        {"PROGRAMME_CHANGE_COOLDOWN_DAYS": "0.5"},
        "PROGRAMME_CHANGE_COOLDOWN_DAYS",
    ),
    ("no-poll-interval", {"LIVE_POLL_FALLBACK_SECONDS": "0"}, "LIVE_POLL_FALLBACK_SECONDS"),
    ("a-negative-spread", {"LIVE_ANNOUNCE_SPREAD_MS": "-1"}, "LIVE_ANNOUNCE_SPREAD_MS"),
    ("no-viewer-refresh", {"LIVE_VIEWER_REFRESH_SECONDS": "0"}, "LIVE_VIEWER_REFRESH_SECONDS"),
)
def test_a_setting_of_the_event_stream_the_cooldown_or_the_browser_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


def test_the_bounds_of_the_event_stream_the_cooldown_and_the_browser_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One topic and thirty-two, a stream one second longer than two beats, an event of
    512 bytes beside the shortest chat message, no cooldown and a whole year, no spread."""
    least = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            LIVE_TOPICS_PER_STREAM="1",
            SSE_HEARTBEAT_SECONDS="1",
            LIVE_STREAM_MAX_SECONDS="3",
            LIVE_EVENT_MAX_BYTES="516",
            CHAT_MESSAGE_MAX_LENGTH="1",
            PROGRAMME_CHANGE_COOLDOWN_DAYS="0",
            LIVE_ANNOUNCE_SPREAD_MS="0",
        )
    )
    most = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            LIVE_TOPICS_PER_STREAM=str(TOPICS_HARD_MAX),
            PROGRAMME_CHANGE_COOLDOWN_DAYS="365",
        )
    )

    assert (least.live.topics_per_stream, most.live.topics_per_stream) == (1, 32)
    assert (least.live.heartbeat_seconds, least.live.stream_max_seconds) == (1, 3)
    assert least.live.event_max_bytes == 516
    assert least.identity.programme_change_cooldown == timedelta(0)
    assert most.identity.programme_change_cooldown_days == 365
    assert least.ui.live_announce_spread_ms == 0


@_breaches(
    (
        "an-event-too-small-for-the-longest-chat-message",
        {"LIVE_EVENT_MAX_BYTES": "1311"},
        "LIVE_EVENT_MAX_BYTES must be at least 4 x CHAT_MESSAGE_MAX_LENGTH + 512",
    ),
    (
        "a-chat-message-too-long-for-an-event",
        {"CHAT_MESSAGE_MAX_LENGTH": "500", "LIVE_EVENT_MAX_BYTES": "2511"},
        "LIVE_EVENT_MAX_BYTES must be at least 4 x CHAT_MESSAGE_MAX_LENGTH + 512",
    ),
    (
        "a-streak-step-that-waits-longer-than-a-trade",
        {"STREAK_LOCK_TIMEOUT_MS": "5001"},
        "STREAK_LOCK_TIMEOUT_MS must not be above POSTGRES_LOCK_TIMEOUT_MS",
    ),
    (
        "a-trade-that-waits-less-than-its-streak-step",
        {"POSTGRES_LOCK_TIMEOUT_MS": "249"},
        "STREAK_LOCK_TIMEOUT_MS must not be above POSTGRES_LOCK_TIMEOUT_MS",
    ),
)
def test_two_sections_that_disagree_fail_loading_and_the_error_names_both_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


def test_two_sections_that_just_agree_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            LIVE_EVENT_MAX_BYTES="1312",
            STREAK_LOCK_TIMEOUT_MS="5000",
        )
    )

    assert loaded.live.event_max_bytes == 4 * loaded.chat.message_max_length + 512
    assert loaded.streaks.lock_timeout_ms == loaded.postgres.lock_timeout_ms


@_breaches(
    ("a-negative-reward", {"STREAK_DAILY_REWARD_CR": "-1"}, "STREAK_DAILY_REWARD_CR"),
    ("a-reward-in-words", {"STREAK_DAILY_REWARD_CR": "two"}, "STREAK_DAILY_REWARD_CR"),
    (
        "a-reward-finer-than-a-micro-credit",
        {"STREAK_DAILY_REWARD_CR": "0.0000001"},
        "STREAK_DAILY_REWARD_CR",
    ),
    ("no-lock-timeout", {"STREAK_LOCK_TIMEOUT_MS": "0"}, "STREAK_LOCK_TIMEOUT_MS"),
    ("a-lock-timeout-with-a-unit", {"STREAK_LOCK_TIMEOUT_MS": "250ms"}, "STREAK_LOCK_TIMEOUT_MS"),
    ("a-milestone-without-a-bonus", {"STREAK_MILESTONES": "7"}, "expected days:bonus, got '7'"),
    (
        "a-milestone-with-two-colons",
        {"STREAK_MILESTONES": "7:10:5"},
        "expected days:bonus, got '7:10:5'",
    ),
    ("a-milestone-of-one-day", {"STREAK_MILESTONES": "1:10"}, "the days of '1:10' must be from 2"),
    (
        "a-milestone-beyond-four-digits",
        {"STREAK_MILESTONES": f"{MILESTONE_MAX_DAYS + 1}:10"},
        f"the days of '{MILESTONE_MAX_DAYS + 1}:10' must be from 2 to {MILESTONE_MAX_DAYS}",
    ),
    (
        "milestone-days-that-are-no-whole-number",
        {"STREAK_MILESTONES": "7.5:10"},
        "the days of '7.5:10' is not a whole number",
    ),
    (
        "milestone-days-with-a-sign",
        {"STREAK_MILESTONES": "+7:10"},
        "the days of '+7:10' is not a whole number",
    ),
    (
        "a-milestone-that-books-nothing",
        {"STREAK_MILESTONES": "7:0"},
        "the bonus of '7:0' must be above zero",
    ),
    (
        "a-negative-bonus",
        {"STREAK_MILESTONES": "7:-5"},
        "the bonus of '7:-5'",
    ),
    (
        "a-bonus-in-words",
        {"STREAK_MILESTONES": "7:ten"},
        "the bonus of '7:ten' is not a valid CR amount",
    ),
    (
        "milestones-out-of-order",
        {"STREAK_MILESTONES": "30:50,7:10"},
        "the days of '7:10' must be above those of the entry before it",
    ),
    (
        "a-milestone-twice",
        {"STREAK_MILESTONES": "7:10,7:20"},
        "the days of '7:20' must be above those of the entry before it",
    ),
    ("no-badge", {"BADGE_LEVELS": ""}, "BADGE_LEVELS"),
    ("a-badge-without-wins", {"BADGE_LEVELS": "Rookie"}, "expected Name:wins, got 'Rookie'"),
    ("a-badge-without-a-name", {"BADGE_LEVELS": ":0"}, "expected Name:wins, got ':0'"),
    (
        "a-first-badge-that-starts-above-zero",
        {"BADGE_LEVELS": "Rookie:1,Oracle:5"},
        "the first badge, 'Rookie', must start at 0 wins",
    ),
    (
        "badges-out-of-order",
        {"BADGE_LEVELS": "Rookie:0,Oracle:40,Analyst:5"},
        "the wins of 'Analyst' must be above those of the entry before it",
    ),
    (
        "two-badges-at-the-same-wins",
        {"BADGE_LEVELS": "Rookie:0,Analyst:5,Oracle:5"},
        "the wins of 'Oracle' must be above those of the entry before it",
    ),
    (
        "a-badge-name-twice-in-another-case",
        {"BADGE_LEVELS": "Rookie:0,ROOKIE:5"},
        "'ROOKIE' is the name of another badge",
    ),
    (
        "a-badge-name-of-twenty-one-characters",
        {"BADGE_LEVELS": f"{'x' * (BADGE_NAME_MAX_LENGTH + 1)}:0"},
        f"is longer than {BADGE_NAME_MAX_LENGTH}",
    ),
    (
        "badge-wins-that-are-no-whole-number",
        {"BADGE_LEVELS": "Rookie:0,Oracle:4.5"},
        "the wins of 'Oracle' is not a whole number",
    ),
    (
        "negative-badge-wins",
        {"BADGE_LEVELS": "Rookie:-1"},
        "the wins of 'Rookie' is not a whole number",
    ),
    (
        "more-wins-than-a-badge-may-ask",
        {"BADGE_LEVELS": "Rookie:0,Oracle:1000001"},
        "the wins of 'Oracle' must be at most 1000000",
    ),
)
def test_a_streak_or_a_badge_setting_that_cannot_work_fails_loading_and_the_error_names_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


@pytest.mark.parametrize("character", ["\t", "\x07", "\x1b"], ids=["tab", "bell", "escape"])
def test_a_badge_name_holds_no_control_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, character: str
) -> None:
    env_file = _example_with(tmp_path, monkeypatch, BADGE_LEVELS=f"'Roo{character}kie:0'")

    with pytest.raises(ValueError, match="holds a control character") as refused:
        Settings.load(env_file)
    assert "BADGE_LEVELS" in str(refused.value)


def test_streaks_may_book_nothing_and_one_badge_is_enough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reward and no milestone count the days all the same; a badge may hold a colon
    and a space, and the longest name has twenty characters."""
    longest = "x" * BADGE_NAME_MAX_LENGTH
    loaded = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            STREAK_DAILY_REWARD_CR="0",
            STREAK_MILESTONES="",
            BADGE_LEVELS=f'"Rookie: year one:0, {longest} :1000000"',
        )
    )
    one = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            STREAK_MILESTONES=f"2:0.000001,{MILESTONE_MAX_DAYS}:1",
            BADGE_LEVELS="Player:0",
        )
    )

    assert (loaded.streaks.daily_reward_micro, loaded.streaks.milestones) == (0, ())
    assert loaded.badges.levels == (
        BadgeLevel(level=1, name="Rookie: year one", wins=0),
        BadgeLevel(level=2, name=longest, wins=1_000_000),
    )
    assert one.streaks.milestones == (
        StreakMilestone(days=2, bonus_micro=1),
        StreakMilestone(days=MILESTONE_MAX_DAYS, bonus_micro=MICRO),
    )
    assert one.badges.levels == (BadgeLevel(level=1, name="Player", wins=0),)
    assert one.badges.level_for(99).name == "Player"
    assert one.badges.next_after(one.badges.levels[0]) is None


SHARE_PRESETS_RULE: Final[str] = (
    "TRADE_SHARE_PRESETS must be one to four whole numbers of shares above zero, in ascending order"
)
SHARE_STEP_RULE: Final[str] = "TRADE_SHARE_STEP must be a whole number of shares above zero"


@_breaches(
    ("no-shortcut", {"TRADE_SHARE_PRESETS": ""}, SHARE_PRESETS_RULE),
    ("five-shortcuts", {"TRADE_SHARE_PRESETS": "1,2,3,4,5"}, SHARE_PRESETS_RULE),
    ("a-shortcut-of-no-share", {"TRADE_SHARE_PRESETS": "0,10"}, SHARE_PRESETS_RULE),
    ("half-a-share", {"TRADE_SHARE_PRESETS": "0.5,10"}, SHARE_PRESETS_RULE),
    ("shortcuts-out-of-order", {"TRADE_SHARE_PRESETS": "50,10,100"}, SHARE_PRESETS_RULE),
    ("a-shortcut-twice", {"TRADE_SHARE_PRESETS": "10,10"}, SHARE_PRESETS_RULE),
    ("a-shortcut-in-words", {"TRADE_SHARE_PRESETS": "ten"}, "TRADE_SHARE_PRESETS"),
    ("a-negative-shortcut", {"TRADE_SHARE_PRESETS": "-10"}, "TRADE_SHARE_PRESETS"),
    ("no-step", {"TRADE_SHARE_STEP": "0"}, SHARE_STEP_RULE),
    ("a-step-of-half-a-share", {"TRADE_SHARE_STEP": "0.5"}, SHARE_STEP_RULE),
    ("a-step-of-one-and-a-half", {"TRADE_SHARE_STEP": "1.5"}, SHARE_STEP_RULE),
    ("a-negative-step", {"TRADE_SHARE_STEP": "-1"}, "TRADE_SHARE_STEP"),
    ("a-step-in-words", {"TRADE_SHARE_STEP": "one"}, "TRADE_SHARE_STEP"),
)
def test_share_shortcuts_or_a_share_step_that_cannot_work_fail_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


@pytest.mark.parametrize(
    ("written", "shares"),
    [("1", [1]), ("1,2,3,4", [1, 2, 3, 4]), ("10.000000,50", [10, 50])],
    ids=["one", "four", "with-decimal-zeros"],
)
def test_one_to_four_ascending_share_shortcuts_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, written: str, shares: list[int]
) -> None:
    env_file = _example_with(
        tmp_path, monkeypatch, TRADE_SHARE_PRESETS=written, TRADE_SHARE_STEP="5"
    )

    pricing = Settings.load(env_file).pricing

    assert pricing.share_presets_micro == [count * MICRO for count in shares]
    assert pricing.share_step_micro == 5 * MICRO


@_breaches(
    (
        "a-question-of-no-character",
        {"DISCUSSION_QUESTION_MIN_LENGTH": "0"},
        "DISCUSSION_QUESTION_MIN_LENGTH",
    ),
    (
        "no-question-at-all",
        {"DISCUSSION_QUESTION_MAX_LENGTH": "0"},
        "DISCUSSION_QUESTION_MAX_LENGTH",
    ),
    (
        "a-question-longer-than-the-column",
        {"DISCUSSION_QUESTION_MAX_LENGTH": str(QUESTION_STORED_MAX_LENGTH + 1)},
        "DISCUSSION_QUESTION_MAX_LENGTH",
    ),
    (
        "the-longest-question-below-the-shortest",
        {"DISCUSSION_QUESTION_MAX_LENGTH": "9"},
        "DISCUSSION_QUESTION_MAX_LENGTH must not be below DISCUSSION_QUESTION_MIN_LENGTH",
    ),
    (
        "the-shortest-question-above-the-longest",
        {"DISCUSSION_QUESTION_MIN_LENGTH": "301"},
        "DISCUSSION_QUESTION_MAX_LENGTH must not be below DISCUSSION_QUESTION_MIN_LENGTH",
    ),
    ("no-answer-at-all", {"DISCUSSION_ANSWER_MAX_LENGTH": "0"}, "DISCUSSION_ANSWER_MAX_LENGTH"),
    (
        "an-answer-longer-than-the-column",
        {"DISCUSSION_ANSWER_MAX_LENGTH": str(ANSWER_STORED_MAX_LENGTH + 1)},
        "DISCUSSION_ANSWER_MAX_LENGTH",
    ),
    (
        "a-market-without-questions",
        {"DISCUSSION_MAX_QUESTIONS_PER_MARKET": "0"},
        "DISCUSSION_MAX_QUESTIONS_PER_MARKET",
    ),
    ("nobody-waits", {"DISCUSSION_MAX_WAITING_PER_USER": "0"}, "DISCUSSION_MAX_WAITING_PER_USER"),
    (
        "more-waiting-of-one-user-than-a-market-holds",
        {"DISCUSSION_MAX_WAITING_PER_USER": "101"},
        "DISCUSSION_MAX_WAITING_PER_USER must not be above DISCUSSION_MAX_QUESTIONS_PER_MARKET",
    ),
    (
        "a-market-that-holds-fewer-than-one-user-may-have-waiting",
        {"DISCUSSION_MAX_QUESTIONS_PER_MARKET": "2"},
        "DISCUSSION_MAX_WAITING_PER_USER must not be above DISCUSSION_MAX_QUESTIONS_PER_MARKET",
    ),
    (
        "no-question-per-window",
        {"DISCUSSION_QUESTION_LIMIT_PER_USER": "0"},
        "DISCUSSION_QUESTION_LIMIT_PER_USER",
    ),
    (
        "a-question-window-of-no-length",
        {"DISCUSSION_QUESTION_LIMIT_PER_USER_WINDOW_SECONDS": "0"},
        "DISCUSSION_QUESTION_LIMIT_PER_USER_WINDOW_SECONDS",
    ),
    ("a-message-of-no-character", {"CHAT_MESSAGE_MAX_LENGTH": "0"}, "CHAT_MESSAGE_MAX_LENGTH"),
    (
        "a-message-longer-than-the-column",
        {"CHAT_MESSAGE_MAX_LENGTH": str(CHAT_MESSAGE_STORED_MAX_LENGTH + 1)},
        "CHAT_MESSAGE_MAX_LENGTH",
    ),
    ("no-message-per-window", {"CHAT_MESSAGE_LIMIT_PER_USER": "0"}, "CHAT_MESSAGE_LIMIT_PER_USER"),
    (
        "a-message-window-of-no-length",
        {"CHAT_MESSAGE_LIMIT_PER_USER_WINDOW_SECONDS": "0"},
        "CHAT_MESSAGE_LIMIT_PER_USER_WINDOW_SECONDS",
    ),
    ("a-chat-that-takes-nothing", {"CHAT_ROOM_MESSAGE_LIMIT": "0"}, "CHAT_ROOM_MESSAGE_LIMIT"),
    (
        "a-chat-window-of-no-length",
        {"CHAT_ROOM_MESSAGE_LIMIT_WINDOW_SECONDS": "0"},
        "CHAT_ROOM_MESSAGE_LIMIT_WINDOW_SECONDS",
    ),
    ("a-read-of-no-message", {"CHAT_BACKLOG_SIZE": "0"}, "CHAT_BACKLOG_SIZE"),
    (
        "a-read-of-more-than-the-most",
        {"CHAT_BACKLOG_SIZE": str(CHAT_BACKLOG_MAX + 1)},
        "CHAT_BACKLOG_SIZE",
    ),
)
def test_a_discussion_or_a_chat_setting_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


def test_the_bounds_of_discussion_and_chat_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            DISCUSSION_QUESTION_MIN_LENGTH=str(QUESTION_STORED_MAX_LENGTH),
            DISCUSSION_QUESTION_MAX_LENGTH=str(QUESTION_STORED_MAX_LENGTH),
            DISCUSSION_ANSWER_MAX_LENGTH=str(ANSWER_STORED_MAX_LENGTH),
            DISCUSSION_MAX_QUESTIONS_PER_MARKET="3",
            DISCUSSION_MAX_WAITING_PER_USER="3",
            CHAT_MESSAGE_MAX_LENGTH=str(CHAT_MESSAGE_STORED_MAX_LENGTH),
            CHAT_BACKLOG_SIZE=str(CHAT_BACKLOG_MAX),
            # The longest message a chat stores fits an event.
            LIVE_EVENT_MAX_BYTES=str(4 * CHAT_MESSAGE_STORED_MAX_LENGTH + 512),
        )
    )

    discussion, chat = loaded.discussion, loaded.chat
    assert (discussion.question_min_length, discussion.question_max_length) == (500, 500)
    assert discussion.answer_max_length == 2000
    assert (discussion.max_waiting_per_user, discussion.max_questions_per_market) == (3, 3)
    assert (chat.message_max_length, chat.backlog_size) == (500, 200)


@_breaches(
    ("no-live-stream", {"BROADCAST_MAX_LIVE": "0"}, "BROADCAST_MAX_LIVE"),
    (
        "no-request-a-day",
        {"BROADCAST_REQUESTS_PER_USER_PER_DAY": "0"},
        "BROADCAST_REQUESTS_PER_USER_PER_DAY",
    ),
    ("a-live-stream-of-no-minute", {"BROADCAST_MAX_MINUTES": "0"}, "BROADCAST_MAX_MINUTES"),
    ("a-stall-of-nine-seconds", {"BROADCAST_STALL_SECONDS": "9"}, "BROADCAST_STALL_SECONDS"),
    (
        "a-stall-as-long-as-the-backlog",
        {"BROADCAST_STALL_SECONDS": "15"},
        "BROADCAST_STALL_SECONDS must be above BROADCAST_UPLOAD_BACKLOG_SECONDS",
    ),
    (
        "a-backlog-as-long-as-the-stall",
        {"BROADCAST_UPLOAD_BACKLOG_SECONDS": "120"},
        "BROADCAST_STALL_SECONDS must be above BROADCAST_UPLOAD_BACKLOG_SECONDS",
    ),
    ("nobody-watches", {"BROADCAST_MAX_VIEWERS": "0"}, "BROADCAST_MAX_VIEWERS"),
    (
        "an-admission-nginx-forgets-at-once",
        {"BROADCAST_ACCESS_CACHE_SECONDS": "0"},
        "BROADCAST_ACCESS_CACHE_SECONDS",
    ),
    (
        "an-admission-kept-above-a-minute",
        {"BROADCAST_ACCESS_CACHE_SECONDS": "61"},
        "BROADCAST_ACCESS_CACHE_SECONDS",
    ),
    (
        "an-admission-kept-as-long-as-a-stall",
        {"BROADCAST_ACCESS_CACHE_SECONDS": "30", "BROADCAST_STALL_SECONDS": "30"},
        "BROADCAST_ACCESS_CACHE_SECONDS must be below BROADCAST_STALL_SECONDS",
    ),
    ("a-piece-of-249-ms", {"BROADCAST_CHUNK_MS": "249"}, "BROADCAST_CHUNK_MS"),
    ("a-piece-of-5001-ms", {"BROADCAST_CHUNK_MS": "5001"}, "BROADCAST_CHUNK_MS"),
    ("a-piece-of-no-byte", {"BROADCAST_CHUNK_MAX_BYTES": "0"}, "BROADCAST_CHUNK_MAX_BYTES"),
    (
        "a-piece-smaller-than-twice-what-one-holds",
        {"BROADCAST_CHUNK_MAX_BYTES": "465999"},
        "BROADCAST_CHUNK_MAX_BYTES must be at least twice what one piece holds",
    ),
    (
        "pieces-too-long-for-their-size",
        {"BROADCAST_CHUNK_MS": "2500"},
        "BROADCAST_CHUNK_MAX_BYTES must be at least twice what one piece holds",
    ),
    (
        "a-capture-rate-too-high-for-a-piece",
        {"BROADCAST_CAPTURE_VIDEO_KBPS": "4200"},
        "BROADCAST_CAPTURE_VIDEO_KBPS",
    ),
    (
        "a-backlog-of-one-second",
        {"BROADCAST_UPLOAD_BACKLOG_SECONDS": "1"},
        "BROADCAST_UPLOAD_BACKLOG_SECONDS",
    ),
    (
        "an-upload-timeout-of-999-ms",
        {"BROADCAST_UPLOAD_TIMEOUT_MS": "999"},
        "BROADCAST_UPLOAD_TIMEOUT_MS",
    ),
    ("an-upload-retry-of-99-ms", {"BROADCAST_UPLOAD_RETRY_MS": "99"}, "BROADCAST_UPLOAD_RETRY_MS"),
    (
        "a-restart-window-of-999-ms",
        {"BROADCAST_RESTART_WINDOW_MS": "999"},
        "BROADCAST_RESTART_WINDOW_MS",
    ),
    ("a-wait-notice-after-999-ms", {"BROADCAST_WAIT_NOTICE_MS": "999"}, "BROADCAST_WAIT_NOTICE_MS"),
    ("a-player-retry-of-99-ms", {"BROADCAST_PLAYER_RETRY_MS": "99"}, "BROADCAST_PLAYER_RETRY_MS"),
    (
        "a-capture-rate-below-what-is-watched",
        {"BROADCAST_CAPTURE_VIDEO_KBPS": "1199"},
        "BROADCAST_CAPTURE_VIDEO_KBPS must not be below BROADCAST_VIDEO_KBPS",
    ),
    (
        "a-watched-rate-above-what-is-captured",
        {"BROADCAST_VIDEO_KBPS": "1801"},
        "BROADCAST_CAPTURE_VIDEO_KBPS must not be below BROADCAST_VIDEO_KBPS",
    ),
    ("an-odd-input-width", {"BROADCAST_INPUT_MAX_WIDTH": "1921"}, "BROADCAST_INPUT_MAX_WIDTH"),
    ("an-input-width-of-638", {"BROADCAST_INPUT_MAX_WIDTH": "638"}, "BROADCAST_INPUT_MAX_WIDTH"),
    ("an-input-width-of-3842", {"BROADCAST_INPUT_MAX_WIDTH": "3842"}, "BROADCAST_INPUT_MAX_WIDTH"),
    ("an-odd-input-height", {"BROADCAST_INPUT_MAX_HEIGHT": "1081"}, "BROADCAST_INPUT_MAX_HEIGHT"),
    ("an-input-height-of-358", {"BROADCAST_INPUT_MAX_HEIGHT": "358"}, "BROADCAST_INPUT_MAX_HEIGHT"),
    (
        "an-input-height-of-2162",
        {"BROADCAST_INPUT_MAX_HEIGHT": "2162"},
        "BROADCAST_INPUT_MAX_HEIGHT",
    ),
    ("an-odd-picture", {"BROADCAST_VIDEO_HEIGHT": "721"}, "BROADCAST_VIDEO_HEIGHT"),
    ("a-picture-of-238-lines", {"BROADCAST_VIDEO_HEIGHT": "238"}, "BROADCAST_VIDEO_HEIGHT"),
    ("a-picture-of-1082-lines", {"BROADCAST_VIDEO_HEIGHT": "1082"}, "BROADCAST_VIDEO_HEIGHT"),
    ("nine-frames-a-second", {"BROADCAST_VIDEO_FPS": "9"}, "BROADCAST_VIDEO_FPS"),
    ("thirty-one-frames-a-second", {"BROADCAST_VIDEO_FPS": "31"}, "BROADCAST_VIDEO_FPS"),
    ("a-picture-at-199-kbit", {"BROADCAST_VIDEO_KBPS": "199"}, "BROADCAST_VIDEO_KBPS"),
    ("a-picture-at-4001-kbit", {"BROADCAST_VIDEO_KBPS": "4001"}, "BROADCAST_VIDEO_KBPS"),
    ("sound-at-31-kbit", {"BROADCAST_AUDIO_KBPS": "31"}, "BROADCAST_AUDIO_KBPS"),
    ("sound-at-193-kbit", {"BROADCAST_AUDIO_KBPS": "193"}, "BROADCAST_AUDIO_KBPS"),
    (
        "a-segment-of-no-second",
        {"BROADCAST_HLS_SEGMENT_SECONDS": "0"},
        "BROADCAST_HLS_SEGMENT_SECONDS",
    ),
    (
        "a-segment-of-seven-seconds",
        {"BROADCAST_HLS_SEGMENT_SECONDS": "7"},
        "BROADCAST_HLS_SEGMENT_SECONDS",
    ),
    (
        "a-playlist-of-two-segments",
        {"BROADCAST_HLS_PLAYLIST_SEGMENTS": "2"},
        "BROADCAST_HLS_PLAYLIST_SEGMENTS",
    ),
    (
        "a-playlist-of-21-segments",
        {"BROADCAST_HLS_PLAYLIST_SEGMENTS": "21"},
        "BROADCAST_HLS_PLAYLIST_SEGMENTS",
    ),
    (
        "a-socket-path-from-nowhere",
        {"BROADCAST_MEDIA_SOCKET": "run/box.sock"},
        "BROADCAST_MEDIA_SOCKET",
    ),
    (
        "a-socket-that-is-a-directory",
        {"BROADCAST_MEDIA_SOCKET": "/run/marketh-media/"},
        "BROADCAST_MEDIA_SOCKET",
    ),
    ("no-socket", {"BROADCAST_MEDIA_SOCKET": ""}, "BROADCAST_MEDIA_SOCKET"),
    (
        "a-media-timeout-of-99-ms",
        {"BROADCAST_MEDIA_TIMEOUT_MS": "99"},
        "BROADCAST_MEDIA_TIMEOUT_MS",
    ),
)
def test_a_live_video_setting_that_cannot_work_fails_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _refused(tmp_path, monkeypatch, values, named)


def test_the_bounds_of_live_video_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    least = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            BROADCAST_STALL_SECONDS="10",
            BROADCAST_UPLOAD_BACKLOG_SECONDS="2",
            BROADCAST_ACCESS_CACHE_SECONDS="1",
            BROADCAST_CHUNK_MS="250",
            BROADCAST_CAPTURE_VIDEO_KBPS="200",
            BROADCAST_VIDEO_KBPS="200",
            BROADCAST_AUDIO_KBPS="32",
            # Twice a piece of 250 ms at 200 + 32 kbit/s: 2 x 7250 bytes.
            BROADCAST_CHUNK_MAX_BYTES="14500",
            BROADCAST_INPUT_MAX_WIDTH="640",
            BROADCAST_INPUT_MAX_HEIGHT="360",
            BROADCAST_VIDEO_HEIGHT="240",
            BROADCAST_VIDEO_FPS="10",
            BROADCAST_HLS_SEGMENT_SECONDS="1",
            BROADCAST_HLS_PLAYLIST_SEGMENTS="3",
            BROADCAST_UPLOAD_TIMEOUT_MS="1000",
            BROADCAST_UPLOAD_RETRY_MS="100",
            BROADCAST_RESTART_WINDOW_MS="1000",
            BROADCAST_WAIT_NOTICE_MS="1000",
            BROADCAST_PLAYER_RETRY_MS="100",
            BROADCAST_MEDIA_TIMEOUT_MS="100",
        )
    ).broadcast
    # The largest piece of the example holds twice a second at the highest rates.
    most = Settings.load(
        _example_with(
            tmp_path,
            monkeypatch,
            BROADCAST_ACCESS_CACHE_SECONDS="60",
            BROADCAST_INPUT_MAX_WIDTH="3840",
            BROADCAST_INPUT_MAX_HEIGHT="2160",
            BROADCAST_VIDEO_HEIGHT="1080",
            BROADCAST_VIDEO_FPS="30",
            BROADCAST_CAPTURE_VIDEO_KBPS="4000",
            BROADCAST_VIDEO_KBPS="4000",
            BROADCAST_AUDIO_KBPS="192",
            BROADCAST_HLS_SEGMENT_SECONDS="6",
            BROADCAST_HLS_PLAYLIST_SEGMENTS="20",
        )
    ).broadcast

    assert (least.stall_seconds, least.upload_backlog_seconds, least.access_cache_seconds) == (
        10,
        2,
        1,
    )
    assert least.chunk_max_bytes == 2 * ((200 + 32) * 125 * 250 // 1000)
    assert least.heartbeat_interval == timedelta(seconds=2.5)
    assert least.seat_seconds == 1 + 1 * 3
    assert (most.input_max_width, most.input_max_height, most.video_height) == (3840, 2160, 1080)
    assert (most.video_kbps, most.audio_kbps, most.video_fps) == (4000, 192, 30)
    assert most.seat_seconds == 60 + 6 * 20
