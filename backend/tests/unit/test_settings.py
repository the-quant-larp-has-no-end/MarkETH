"""`.env`, `.example.env` and `Settings` describe the same variables (golden rule 7).

Every tunable lives in the environment file and has exactly one typed field in
`Settings`; nothing has a default in code. These tests fail when the three drift apart.
"""

import re
from datetime import timedelta
from pathlib import Path
from typing import Final

import pytest
from sqlalchemy import String

from app.core.config import (
    DEFAULT_ENV_FILE,
    INFRA_ONLY_VARIABLES,
    PRODUCT_ID_MAX_LENGTH,
    PRODUCT_NAME_MAX_LENGTH,
    REPO_ROOT,
    Settings,
    VoucherProduct,
)
from app.core.enums import MarketTier, Outcome
from app.core.identity import USERNAME_PATTERN
from app.core.units import MICRO
from app.models import Voucher
from app.pricing import lmsr
from tests.support.settings import TEST_ADMIN_EMAILS, TEST_BARISTA_EMAILS

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


def test_the_tests_run_with_two_admins_and_one_barista_where_the_example_names_one_admin(
    base_settings: Settings,
) -> None:
    """An admin who traded a market may not settle it (A.4), so a test of that rule
    needs a second admin. The role lists are the only thing `base_settings` does not
    take from `.example.env`."""
    example = Settings.load(EXAMPLE_ENV_FILE)
    identity = base_settings.identity

    assert identity.admin_emails == list(TEST_ADMIN_EMAILS)
    assert identity.barista_emails == list(TEST_BARISTA_EMAILS)
    assert (len(set(identity.admin_emails)), len(identity.barista_emails)) == (2, 1)
    assert not set(identity.admin_emails) & set(identity.barista_emails)
    assert len(example.identity.admin_emails) == 1, "the example keeps one admin"

    changed = {"admin_emails", "barista_emails"}
    assert identity.model_dump(exclude=changed) == example.identity.model_dump(exclude=changed)
    assert base_settings.model_dump(exclude={"identity", "app"}) == example.model_dump(
        exclude={"identity", "app"}
    )
    assert base_settings.app.model_dump(exclude={"env"}) == example.app.model_dump(exclude={"env"})
    assert base_settings.app.env == "test"


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


def test_domains_are_read_in_lower_case_and_role_entries_the_way_an_id_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain has one spelling, and so has an id: a role list holds the ids of its
    people as the portal's id is read, in lower case and at the domain written."""
    env_file = _example_with(
        tmp_path,
        monkeypatch,
        ALLOWED_EMAIL_DOMAINS="Student.ETHZ.ch, ethz.CH",
        ADMIN_EMAILS="Ada@ETHZ.ch, grace@student.ethz.ch",
        BARISTA_EMAILS="",
    )

    identity = Settings.load(env_file).identity

    assert identity.allowed_email_domains == ["student.ethz.ch", "ethz.ch"]
    assert identity.admin_emails == ["ada@ethz.ch", "grace@student.ethz.ch"]
    assert identity.barista_emails == []


@pytest.mark.parametrize("variable", ["ADMIN_EMAILS", "BARISTA_EMAILS"])
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
def test_a_role_entry_that_is_no_id_fails_loading_and_the_error_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str, entry: str
) -> None:
    """An entry the id rule refuses would give its role to nobody: a typing mistake in
    `.env` that shows as a missing admin. The process does not start on it."""
    env_file = _example_with(tmp_path, monkeypatch, **{variable: f"admin@ethz.ch,{entry}"})

    with pytest.raises(ValueError, match=variable) as refused:
        Settings.load(env_file)

    message = str(refused.value)
    assert "1 validation error" in message
    assert repr(entry) in message
    assert "is not an address at ethz.ch" in message


def test_a_role_list_may_be_empty_and_a_reserved_list_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is a barista where no address is one; an admin redeems vouchers too."""
    env_file = _example_with(tmp_path, monkeypatch, BARISTA_EMAILS="", RESERVED_USERNAMES="")

    identity = Settings.load(env_file).identity

    assert (identity.barista_emails, identity.reserved_usernames) == ([], [])
    assert identity.admin_emails == ["admin@ethz.ch"]


def test_local_env_has_the_same_keys_as_the_example() -> None:
    """`.env` is gitignored; where a developer has one, it must not have drifted."""
    if not DEFAULT_ENV_FILE.exists():
        pytest.skip("no local .env")
    assert set(variables_of(DEFAULT_ENV_FILE)) == set(variables_of(EXAMPLE_ENV_FILE))
