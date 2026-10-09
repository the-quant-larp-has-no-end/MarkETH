"""`.env`, `.example.env` and `Settings` describe the same variables (golden rule 7).

Every tunable lives in the environment file and has exactly one typed field in
`Settings`; nothing has a default in code. These tests fail when the three drift apart.
"""

import re
from pathlib import Path
from typing import Final

import pytest

from app.core.config import DEFAULT_ENV_FILE, INFRA_ONLY_VARIABLES, REPO_ROOT, Settings
from app.core.enums import MarketTier
from app.core.units import MICRO

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"

_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.MULTILINE)


def variables_of(env_file: Path) -> list[str]:
    """Names assigned in an env file, in file order, duplicates included."""
    return _ASSIGNMENT.findall(env_file.read_text(encoding="utf-8"))


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
    assert base_settings.vouchers.signup_grant_micro == 5 * MICRO
    assert base_settings.vouchers.monthly_grant_micro == MICRO
    assert base_settings.pricing.max_stake_per_user_market_micro == MICRO
    assert base_settings.pricing.min_trade_micro == 10_000
    assert base_settings.pricing.stake_presets_micro == [250_000, 500_000, MICRO]
    assert base_settings.pricing.tier_b_micro(MarketTier.DEFAULT) == 10 * MICRO
    assert base_settings.risk.max_aggregate_loss_budget_micro == 700 * MICRO


def test_example_env_lists_and_derived_values(base_settings: Settings) -> None:
    assert base_settings.auth.allowed_email_domains == ["student.ethz.ch", "ethz.ch"]
    assert base_settings.auth.canonical_email_domain == "ethz.ch"
    assert base_settings.app.timezone.key == "Europe/Zurich"
    assert base_settings.auth.session_ttl.days == 30
    assert base_settings.redis.timeout_seconds == 0.25
    assert base_settings.scylla.hosts == ["localhost"]


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


def test_local_env_has_the_same_keys_as_the_example() -> None:
    """`.env` is gitignored; where a developer has one, it must not have drifted."""
    if not DEFAULT_ENV_FILE.exists():
        pytest.skip("no local .env")
    assert set(variables_of(DEFAULT_ENV_FILE)) == set(variables_of(EXAMPLE_ENV_FILE))
