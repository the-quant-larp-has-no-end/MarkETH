"""What a form is told about a field that was filled in wrongly (SPEC §10, Appendix A.2).

A 422 lists one `{loc, msg, type}` per field, and a screen shows `msg` under that field
as it is. So `msg` is a sentence for the person who typed: short, saying what to do,
ending with a full stop, and never the framework's wording ("String should match
pattern ...").

Three things are held here:

* the table of `app/api/validation.py`, rule by rule;
* the helpers with which a field says a sentence of its own (`said`,
  `in_its_own_words`, `on_field`), and the sentences the request models say with them;
* every field of every request model and query model the API has, sent wrong through
  the real application: whatever it is refused with is a sentence.

Nothing here needs a data store. The application runs on a container that connects to
nothing, in which every service is a stand-in that says it was reached: a request that
passes its validation ends there.
"""

import importlib
import inspect
import pkgutil
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Final, Self
from uuid import uuid7

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

import app as application
from app import schemas
from app.api.deps import authenticate_request
from app.api.validation import NEUTRAL, SENTENCES, sentence_of
from app.container import Container
from app.core import errors as core_errors
from app.core.config import Settings
from app.core.enums import UserRole, UserStatus
from app.core.errors import AppError, ConflictError
from app.core.identity import AuthenticatedUser, Caller, PortalIdentity
from app.core.units import MICRO
from app.main import create_app
from app.schemas.admin.code_batches import CreateCodeBatchRequest, RevokeCodesRequest
from app.schemas.admin.markets import (
    BUDGET_NEEDED_MESSAGE,
    BUDGET_NOT_TAKEN_MESSAGE,
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.admin.users import (
    AdjustBalanceRequest,
    AdminUserListQuery,
    ReinstateUserRequest,
    SetUserRoleRequest,
    SuspendUserRequest,
)
from app.schemas.barista import RedeemVoucherRequest
from app.schemas.codes import RedeemCodeRequest
from app.schemas.common import (
    ACCEPT_THE_POLICY_MESSAGE,
    AMOUNT_NEGATIVE_MESSAGE,
    AMOUNT_NOT_POSITIVE_MESSAGE,
    AMOUNT_TOO_LARGE_MESSAGE,
    AMOUNT_ZERO_MESSAGE,
    NOT_AN_AMOUNT_MESSAGE,
    SENTENCE,
    TOO_MANY_PLACES_MESSAGE,
    CreditAmount,
    NonZeroCreditAmount,
    PageQuery,
    PositiveCreditAmount,
    RequestModel,
    in_its_own_words,
    on_field,
    said,
)
from app.schemas.leaderboard import (
    NOT_A_PERCENTAGE_MESSAGE,
    NOT_A_RATIO_MESSAGE,
    LeaderboardQuery,
)
from app.schemas.markets import DECIDED_BEFORE_CLOSE_MESSAGE, ProposeMarketRequest
from app.schemas.profile import USERNAME_CHARACTERS_MESSAGE, SaveProfileRequest
from app.schemas.trading import (
    GUARD_DOES_NOT_APPLY_MESSAGE,
    SELL_BY_SHARES_MESSAGE,
    SIZED_ONE_WAY_MESSAGE,
    QuoteQuery,
    TradeRequest,
    TradeSize,
)
from app.schemas.vouchers import IssueVoucherRequest
from app.services.admin_users import adjustment_limit_message
from app.services.codes import code_value_message
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.containers import LOOPBACK, closed_port

FRAMEWORK_WORDING: Final[tuple[str, ...]] = (
    "String should",
    "Input should",
    "Value error",
    "Field required",
    "Decimal input",
    "Extra inputs",
    "Input is not",
    "Assertion failed",
    "JSON decode error",
    "List should",
    "Unable to",
)
"""How the framework's own messages begin. No `msg` may begin like one of them."""

NOT_ON_SCREEN: Final[re.Pattern[str]] = re.compile(r"credit|coffee|micro|\bCR\b|µ", re.IGNORECASE)
"""What no sentence says. On screen a credit is a token, and nobody is told a unit of
the database."""

LONGEST_SENTENCE: Final[int] = 100
"""Characters. A sentence under a field of a phone is a line or two."""


def assert_is_a_sentence(msg: object, where: object = "") -> None:
    """`msg` is something a screen can show to a person as it is."""
    assert isinstance(msg, str), where
    assert msg, where
    assert not msg.startswith(FRAMEWORK_WORDING), f"the framework's wording, {where}: {msg!r}"
    assert msg[0].isupper(), f"{where}: {msg!r}"
    assert msg.endswith("."), f"no full stop, {where}: {msg!r}"
    assert not msg.endswith(".."), f"{where}: {msg!r}"
    assert "\n" not in msg, f"{where}: {msg!r}"
    assert len(msg) <= LONGEST_SENTENCE, f"not short, {where}: {msg!r}"
    assert NOT_ON_SCREEN.search(msg) is None, f"not a word for the screen, {where}: {msg!r}"


# --- The table --------------------------------------------------------------------------


def reported(kind: str, **context: object) -> dict[str, Any]:
    """An error as the framework reports it: its name of the rule, its own message and
    the figures of the rule."""
    error: dict[str, Any] = {"type": kind, "loc": ("body", "field"), "msg": "Input should not"}
    if context:
        error["ctx"] = context
    return error


FIGURES_OF: Final[dict[str, dict[str, object]]] = {
    "string_too_short": {"min_length": 10},
    "string_too_long": {"max_length": 200},
    "greater_than_equal": {"ge": 0},
    "less_than_equal": {"le": 200},
    "greater_than": {"gt": 0},
    "less_than": {"lt": 5},
    "too_short": {"min_length": 1, "field_type": "List", "actual_length": 0},
    "too_long": {"max_length": 10_000, "field_type": "List", "actual_length": 10_001},
}
"""What the framework says besides the name of a rule, for the rules that have figures."""


@pytest.mark.parametrize("kind", sorted(SENTENCES))
def test_every_rule_of_the_table_is_answered_with_a_sentence(kind: str) -> None:
    sentence = sentence_of(reported(kind, **FIGURES_OF.get(kind, {})))

    assert_is_a_sentence(sentence, kind)
    assert sentence != NEUTRAL


@pytest.mark.parametrize(
    ("kind", "figures", "sentence"),
    [
        ("missing", {}, "This is required."),
        ("extra_forbidden", {}, "This field is not part of the request."),
        ("string_type", {}, "Enter text."),
        ("string_too_short", {"min_length": 1}, "This cannot be empty."),
        ("string_too_short", {"min_length": 2}, "Use at least 2 characters."),
        ("string_too_short", {"min_length": 10}, "Use at least 10 characters."),
        ("string_too_long", {"max_length": 1}, "Use at most 1 character."),
        ("string_too_long", {"max_length": 200}, "Use at most 200 characters."),
        ("string_too_long", {"max_length": 2000}, "Use at most 2,000 characters."),
        (
            "string_pattern_mismatch",
            {"pattern": "^x$"},
            "Use only the characters this field takes.",
        ),
        ("int_parsing", {}, "Enter a whole number."),
        ("int_type", {}, "Enter a whole number."),
        ("int_from_float", {}, "Enter a whole number."),
        ("int_parsing_size", {}, "Enter a smaller number."),
        ("float_parsing", {}, "Enter a number."),
        ("finite_number", {}, "Enter a number."),
        ("greater_than_equal", {"ge": 0}, "Enter 0 or more."),
        ("greater_than_equal", {"ge": 1}, "Enter 1 or more."),
        ("less_than_equal", {"le": 1}, "Enter 1 or less."),
        ("less_than_equal", {"le": 1.0}, "Enter 1 or less."),
        ("less_than_equal", {"le": 0.5}, "Enter 0.5 or less."),
        ("less_than_equal", {"le": 200}, "Enter 200 or less."),
        ("less_than_equal", {"le": 10_000}, "Enter 10,000 or less."),
        ("less_than_equal", {"le": 1_000_000_000}, "Enter 1,000,000,000 or less."),
        ("greater_than", {"gt": 0}, "Enter more than 0."),
        ("less_than", {"lt": 5}, "Enter less than 5."),
        ("bool_type", {}, "Choose yes or no."),
        ("enum", {"expected": "'YES' or 'NO'"}, "Choose one of the options."),
        ("uuid_parsing", {"error": "invalid character"}, "This is not a valid id."),
        ("datetime_from_date_parsing", {"error": "too short"}, "Enter a date and a time."),
        ("timezone_aware", {}, "Enter a date and a time with its time zone."),
        ("list_type", {}, "Give a list."),
        ("too_short", {"min_length": 1}, "Give at least 1 entry."),
        ("too_long", {"max_length": 10_000}, "Give at most 10,000 entries."),
        ("json_invalid", {"error": "Expecting value"}, "The request is not valid JSON."),
        ("model_attributes_type", {}, "Send the fields of the request as one JSON object."),
    ],
)
def test_the_table_says_what_to_do_with_the_figure_of_the_rule(
    kind: str, figures: dict[str, object], sentence: str
) -> None:
    assert sentence_of(reported(kind, **figures)) == sentence


@pytest.mark.parametrize(
    "error",
    [
        reported("value_error", error=ValueError("resolve_by must not be before close_at")),
        reported("assertion_error", error=AssertionError("x")),
        reported("a_rule_nobody_wrote_a_sentence_for"),
        reported("string_too_short"),
        reported("less_than_equal", ge=3),
        {"type": "decimal_parsing", "loc": ("body",), "msg": "Input should be a valid decimal"},
    ],
    ids=[
        "an-error-a-validator-raised-bare",
        "an-assertion",
        "an-unknown-rule",
        "a-length-without-its-figure",
        "a-bound-without-its-figure",
        "a-rule-of-the-framework-the-table-lacks",
    ],
)
def test_a_rule_the_table_cannot_word_gets_the_neutral_sentence_never_the_frameworks(
    error: dict[str, Any],
) -> None:
    assert sentence_of(error) == NEUTRAL == "This value is not valid."
    assert_is_a_sentence(NEUTRAL)


def test_a_sentence_a_field_says_itself_is_used_as_it_is() -> None:
    """Also for a rule the table has a sentence for: the field knows better."""
    own = "Use lower-case letters, digits and underscores only."

    assert sentence_of(reported("string_pattern_mismatch", **{SENTENCE: own})) == own
    assert sentence_of(reported("string_pattern_mismatch", pattern="x")) != own
    assert sentence_of(reported("a_rule_of_ours", **{SENTENCE: own})) == own


# --- A field in its own words -----------------------------------------------------------


class Form(BaseModel):
    """A request with a field of each kind that words its own errors."""

    name: Annotated[
        str,
        Field(min_length=2, max_length=5, pattern="^[a-z]+$"),
        in_its_own_words(string_pattern_mismatch="Use small letters only."),
    ]
    share: (
        Annotated[
            float,
            Field(ge=0, le=1),
            in_its_own_words(less_than_equal="Enter a percentage between 0 and 100."),
        ]
        | None
    ) = None
    lowest: int | None = None
    highest: int | None = None

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        if self.lowest is not None and self.highest is not None and self.lowest > self.highest:
            raise on_field("lowest", "value_error", "Enter a lower bound below the upper one.")
        if self.lowest == 13:
            raise said("unlucky", "Pick another number.")
        return self


def errors_of(model: type[BaseModel], data: dict[str, Any]) -> list[dict[str, Any]]:
    """What `model` refuses `data` with, as the framework reports it."""
    with pytest.raises(ValidationError) as refused:
        model.model_validate(data)
    return [dict(error) for error in refused.value.errors(include_url=False)]


def said_for(model: type[BaseModel], data: dict[str, Any]) -> dict[tuple[Any, ...], str]:
    """The sentence a client is shown for each field `model` refuses in `data`."""
    return {tuple(error["loc"]): sentence_of(error) for error in errors_of(model, data)}


def test_a_field_says_its_own_sentence_for_the_rules_it_names_and_keeps_their_type() -> None:
    (pattern,) = errors_of(Form, {"name": "Anna"})
    (bound,) = errors_of(Form, {"name": "anna", "share": 1.5})

    assert (pattern["loc"], pattern["type"]) == (("name",), "string_pattern_mismatch")
    assert pattern["msg"] == sentence_of(pattern) == "Use small letters only."
    assert (bound["loc"], bound["type"]) == (("share",), "less_than_equal")
    assert bound["msg"] == sentence_of(bound) == "Enter a percentage between 0 and 100."


def test_a_rule_the_field_has_no_words_for_passes_to_the_table() -> None:
    (short,) = errors_of(Form, {"name": "a"})
    (negative,) = errors_of(Form, {"name": "anna", "share": -1})

    assert (short["type"], short["ctx"]) == ("string_too_short", {"min_length": 2})
    assert short["msg"].startswith("String should"), "the framework's, until the table words it"
    assert sentence_of(short) == "Use at least 2 characters."
    assert sentence_of(negative) == "Enter 0 or more."


def test_words_of_its_own_leave_the_schema_of_a_field_as_it_is() -> None:
    """The rules are still the field's: a client reads the pattern and the bounds."""
    fields = Form.model_json_schema()["properties"]

    assert fields["name"] == {
        "type": "string",
        "minLength": 2,
        "maxLength": 5,
        "pattern": "^[a-z]+$",
        "title": "Name",
    }
    assert fields["share"]["anyOf"] == [
        {"type": "number", "minimum": 0, "maximum": 1},
        {"type": "null"},
    ]
    assert Form.model_validate({"name": "anna", "share": 0.5}).share == 0.5


def test_a_rule_about_several_fields_is_reported_on_the_one_it_names() -> None:
    (out_of_order,) = errors_of(Form, {"name": "anna", "lowest": 5, "highest": 3})
    (whole_request,) = errors_of(Form, {"name": "anna", "lowest": 13})

    assert (out_of_order["loc"], out_of_order["type"]) == (("lowest",), "value_error")
    assert sentence_of(out_of_order) == "Enter a lower bound below the upper one."
    # Raised without a field, an error is about the request as a whole.
    assert (whole_request["loc"], whole_request["type"]) == ((), "unlucky")
    assert sentence_of(whole_request) == "Pick another number."


def test_an_error_in_a_fields_own_words_carries_the_sentence_as_its_message() -> None:
    error = said("decimal_places", "Use at most 6 decimal places.")

    assert (error.type, error.message()) == ("decimal_places", "Use at most 6 decimal places.")
    assert error.context == {SENTENCE: "Use at most 6 decimal places."}


# --- The sentences the request models say -----------------------------------------------

PROFILE: Final[dict[str, object]] = {
    "username": "neo",
    "degree_level": "OTHER",
    "degree_programme": "ETH staff",
    "policy_accepted": True,
}

PROPOSAL: Final[dict[str, object]] = {
    "question": "Will the Polymensa serve Älplermagronen on Friday?",
    "yes_definition": "It is on the menu.",
    "no_definition": "It is not on the menu.",
    "resolution_criteria": "The published menu of that day.",
    "resolution_source": "ethz.ch/menu",
    "close_at": (TEST_NOW + timedelta(days=7)).isoformat(),
    "resolve_by": (TEST_NOW + timedelta(days=9)).isoformat(),
    "policy_accepted": True,
}

BUY: Final[dict[str, object]] = {"side": "BUY", "outcome": "YES", "amount_cr": "25"}
SELL: Final[dict[str, object]] = {"side": "SELL", "outcome": "YES", "shares": "2"}


class Amounts(BaseModel):
    """One field of each kind of amount a request can carry."""

    at_least_zero: CreditAmount | None = None
    above_zero: PositiveCreditAmount | None = None
    not_zero: NonZeroCreditAmount | None = None


@pytest.mark.parametrize(
    ("data", "sentence"),
    [
        ({"at_least_zero": "abc"}, NOT_AN_AMOUNT_MESSAGE),
        ({"at_least_zero": 12.5}, NOT_AN_AMOUNT_MESSAGE),
        ({"at_least_zero": "1,5"}, NOT_AN_AMOUNT_MESSAGE),
        ({"at_least_zero": "1e3"}, NOT_AN_AMOUNT_MESSAGE),
        ({"at_least_zero": ""}, NOT_AN_AMOUNT_MESSAGE),
        ({"at_least_zero": "0.0000001"}, TOO_MANY_PLACES_MESSAGE),
        ({"at_least_zero": "1000000000000"}, AMOUNT_TOO_LARGE_MESSAGE),
        ({"at_least_zero": "-3"}, AMOUNT_NEGATIVE_MESSAGE),
        ({"above_zero": "0"}, AMOUNT_NOT_POSITIVE_MESSAGE),
        ({"above_zero": "-3"}, AMOUNT_NOT_POSITIVE_MESSAGE),
        ({"above_zero": "1000000000"}, AMOUNT_TOO_LARGE_MESSAGE),
        ({"not_zero": "0"}, AMOUNT_ZERO_MESSAGE),
        ({"not_zero": "-0.000000"}, AMOUNT_ZERO_MESSAGE),
    ],
)
def test_an_amount_says_what_is_wrong_with_it(data: dict[str, object], sentence: str) -> None:
    (said_on_the_field,) = said_for(Amounts, data).values()

    assert said_on_the_field == sentence
    assert_is_a_sentence(sentence)


def test_the_sentences_of_an_amount_read_as_they_are_shown() -> None:
    assert NOT_AN_AMOUNT_MESSAGE == "Enter an amount, for example 12.5."
    assert TOO_MANY_PLACES_MESSAGE == "Use at most 6 decimal places."
    assert AMOUNT_TOO_LARGE_MESSAGE == "Enter a smaller amount."
    assert AMOUNT_NOT_POSITIVE_MESSAGE == "Enter an amount above 0."
    assert AMOUNT_NEGATIVE_MESSAGE == "Enter an amount of 0 or more."
    assert AMOUNT_ZERO_MESSAGE == "Enter an amount other than 0."


@pytest.mark.parametrize(
    "username", ["anna-m", "max.muster", "jürg", "anna m", "Anna", "ANNA", "a@b"]
)
def test_a_username_says_which_characters_it_takes(username: str) -> None:
    """A hyphen, a dot, an umlaut, a space, a capital letter: one sentence, and not the
    regular expression the field is held to."""
    (error,) = errors_of(SaveProfileRequest, PROFILE | {"username": username})

    assert (error["loc"], error["type"]) == (("username",), "string_pattern_mismatch")
    assert sentence_of(error) == USERNAME_CHARACTERS_MESSAGE
    assert USERNAME_CHARACTERS_MESSAGE == "Use lower-case letters, digits and underscores only."
    assert "^" not in sentence_of(error)


def test_a_username_of_the_wrong_length_is_told_so_by_the_table() -> None:
    assert said_for(SaveProfileRequest, PROFILE | {"username": "  "}) == {
        ("username",): "This cannot be empty."
    }
    assert said_for(SaveProfileRequest, PROFILE | {"username": "x" * 33}) == {
        ("username",): "Use at most 32 characters."
    }
    assert said_for(SaveProfileRequest, PROFILE | {"degree_level": "KINDERGARTEN"}) == {
        ("degree_level",): "Choose one of the options."
    }


@pytest.mark.parametrize("bound", ["min_win_rate", "max_win_rate"])
@pytest.mark.parametrize("sent", ["1.5", "150", "-0.1", "abc", "nan", "inf", "", "50%"])
def test_a_win_rate_is_asked_for_in_the_unit_it_is_typed_in(bound: str, sent: str) -> None:
    """The filter is typed in percent and sent as a fraction: 150 % arrives as 1.5. The
    sentence speaks of percentages, where the rule speaks of 1."""
    (error,) = errors_of(LeaderboardQuery, {bound: sent})

    assert error["loc"] == (bound,)
    assert error["type"] in {
        "less_than_equal",
        "greater_than_equal",
        "float_parsing",
        "finite_number",
    }
    assert sentence_of(error) == NOT_A_PERCENTAGE_MESSAGE == "Enter a percentage between 0 and 100."


def test_the_other_bounds_of_the_leaderboard_say_what_to_type() -> None:
    def sentence(field: str, sent: str) -> str:
        (told,) = said_for(LeaderboardQuery, {field: sent}).values()
        return told

    assert sentence("min_avg_payout_ratio", "abc") == NOT_A_RATIO_MESSAGE
    assert NOT_A_RATIO_MESSAGE == "Enter a number, for example 1.25."
    assert sentence("max_avg_payout_ratio", "-1") == "Enter 0 or more."
    assert sentence("max_avg_payout_ratio", "1000001") == "Enter 1,000,000 or less."
    assert sentence("min_trades", "2.5") == "Enter a whole number."
    assert sentence("min_trades", "many") == "Enter a whole number."
    assert sentence("max_trades", "-1") == "Enter 0 or more."
    assert sentence("max_trades", "1000000001") == "Enter 1,000,000,000 or less."
    assert sentence("min_credits_cr", "lots") == NOT_AN_AMOUNT_MESSAGE
    assert sentence("max_credits_cr", "0.1234567") == TOO_MANY_PLACES_MESSAGE
    assert sentence("degree_programme", "") == "This cannot be empty."
    assert sentence("sort", "luck") == "Choose one of the options."
    assert sentence("limit", "0") == "Enter 1 or more."
    assert sentence("limit", "201") == "Enter 200 or less."


def test_the_texts_and_dates_of_a_proposal_say_what_is_asked_of_them() -> None:
    def sentences(**changed: object) -> dict[tuple[Any, ...], str]:
        return said_for(ProposeMarketRequest, PROPOSAL | changed)

    assert sentences(question="Too short") == {("question",): "Use at least 10 characters."}
    assert sentences(question="x" * 201) == {("question",): "Use at most 200 characters."}
    assert sentences(yes_definition="   ") == {("yes_definition",): "This cannot be empty."}
    assert sentences(no_definition="x" * 501) == {("no_definition",): "Use at most 500 characters."}
    assert sentences(resolution_criteria="x" * 2001) == {
        ("resolution_criteria",): "Use at most 2,000 characters."
    }
    assert sentences(close_at="next Friday") == {("close_at",): "Enter a date and a time."}
    assert sentences(close_at="2026-10-16T12:00:00") == {
        ("close_at",): "Enter a date and a time with its time zone."
    }
    assert sentences(resolve_by=(TEST_NOW + timedelta(days=6)).isoformat()) == {
        ("resolve_by",): DECIDED_BEFORE_CLOSE_MESSAGE
    }
    assert DECIDED_BEFORE_CLOSE_MESSAGE == "A market cannot be decided before trading closes."
    for declined in (False, "true", 1, None):
        assert sentences(policy_accepted=declined) == {
            ("policy_accepted",): ACCEPT_THE_POLICY_MESSAGE
        }
    assert ACCEPT_THE_POLICY_MESSAGE == "Accept the usage policy to continue."
    without = dict(PROPOSAL)
    del without["policy_accepted"]
    assert said_for(ProposeMarketRequest, without) == {("policy_accepted",): "This is required."}


def test_the_reasons_and_notes_of_admin_actions_say_what_is_asked_of_them() -> None:
    for model, field in (
        (RejectMarketRequest, "reason"),
        (HaltMarketRequest, "reason"),
        (SuspendUserRequest, "reason"),
        (ReinstateUserRequest, "reason"),
        (ResumeMarketRequest, "note"),
        (CloseMarketRequest, "note"),
    ):
        assert said_for(model, {field: "  "}) == {(field,): "This cannot be empty."}, model
        assert said_for(model, {field: "x" * 501}) == {(field,): "Use at most 500 characters."}
    assert said_for(VoidMarketRequest, {"reason": "x" * 2001}) == {
        ("reason",): "Use at most 2,000 characters."
    }
    assert said_for(ResolveMarketRequest, {"outcome": "MAYBE", "evidence": ""}) == {
        ("outcome",): "Choose one of the options.",
        ("evidence",): "This cannot be empty.",
    }
    assert said_for(RejectMarketRequest, {}) == {("reason",): "This is required."}


def test_a_batch_of_codes_says_what_is_asked_of_its_count_and_its_label() -> None:
    batch = {"label": "Apéro", "count": 5, "expires_at": PROPOSAL["close_at"], "one_per_user": True}

    def sentences(**changed: object) -> dict[tuple[Any, ...], str]:
        return said_for(CreateCodeBatchRequest, batch | changed)

    assert sentences(count="5") == {("count",): "Enter a whole number."}
    assert sentences(count=5.5) == {("count",): "Enter a whole number."}
    assert sentences(count=0) == {("count",): "Enter 1 or more."}
    assert sentences(count=10_001) == {("count",): "Enter 10,000 or less."}
    assert sentences(label="") == {("label",): "This cannot be empty."}
    assert sentences(label="x" * 101) == {("label",): "Use at most 100 characters."}
    assert sentences(one_per_user="yes") == {("one_per_user",): "Choose yes or no."}
    assert sentences(value_cr="0") == {("value_cr",): AMOUNT_NOT_POSITIVE_MESSAGE}
    assert sentences(expires_at="tomorrow") == {("expires_at",): "Enter a date and a time."}


def test_a_trade_that_is_sized_or_guarded_the_wrong_way_names_the_field_that_is_wrong() -> None:
    """No form sends one of these. Whoever builds a client is told which field it is."""
    assert said_for(TradeRequest, {"side": "BUY", "outcome": "YES"}) == {(): SIZED_ONE_WAY_MESSAGE}
    assert said_for(QuoteQuery, BUY | {"shares": "1"}) == {(): SIZED_ONE_WAY_MESSAGE}
    assert said_for(QuoteQuery, {"side": "SELL", "outcome": "NO", "amount_cr": "1"}) == {
        ("amount_cr",): SELL_BY_SHARES_MESSAGE
    }
    assert said_for(TradeRequest, BUY | {"max_cost_cr": "1"}) == {
        ("max_cost_cr",): GUARD_DOES_NOT_APPLY_MESSAGE
    }
    assert said_for(TradeRequest, SELL | {"min_shares": "1"}) == {
        ("min_shares",): GUARD_DOES_NOT_APPLY_MESSAGE
    }
    for sentence in (SIZED_ONE_WAY_MESSAGE, SELL_BY_SHARES_MESSAGE, GUARD_DOES_NOT_APPLY_MESSAGE):
        assert_is_a_sentence(sentence)


def test_an_approval_says_on_the_budget_when_it_is_missing_and_when_it_is_too_much() -> None:
    assert said_for(ApproveMarketRequest, {"tier": "CUSTOM"}) == {
        ("budget_cr",): BUDGET_NEEDED_MESSAGE
    }
    assert said_for(ApproveMarketRequest, {"tier": "TIGHT", "budget_cr": "50"}) == {
        ("budget_cr",): BUDGET_NOT_TAKEN_MESSAGE
    }
    assert BUDGET_NEEDED_MESSAGE == "Enter the budget of a custom market."
    assert BUDGET_NOT_TAKEN_MESSAGE == "Leave the budget out unless the tier is custom."


def test_no_validator_of_a_request_model_raises_a_bare_error() -> None:
    """A `ValueError` raised in a validator reaches a client as "Value error, ...".
    The schema modules raise the helpers instead, which carry a sentence."""
    for module in walk_modules(schemas):
        source = inspect.getsource(module)
        assert "raise ValueError" not in source, module.__name__
        assert "raise AssertionError" not in source, module.__name__
        raised_here = source.replace("return PydanticCustomError(", "")
        assert "PydanticCustomError(" not in raised_here, (
            f"{module.__name__}: raise `said(...)`, which carries the sentence"
        )


# --- Every sentence the API can say -----------------------------------------------------


def walk_modules(package: Any) -> Iterator[Any]:
    """Every module of a package, the package's own included."""
    yield package
    for found in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        yield importlib.import_module(found.name)


def every_message() -> Iterator[tuple[str, str]]:
    """Each sentence the application has a name for: the `…_MESSAGE` constants of every
    module, and the default message of every error."""
    for module in walk_modules(application):
        for name, value in vars(module).items():
            if name.endswith("_MESSAGE") and isinstance(value, str):
                yield f"{module.__name__}.{name}", value
    for name, value in vars(core_errors).items():
        if isinstance(value, type) and issubclass(value, AppError):
            yield f"{name}.default_message", value.default_message


def test_every_sentence_the_application_has_a_name_for_is_one_for_the_screen() -> None:
    """ "token" is the word for a credit on screen: no message says "credit", "CR" or
    "coffee". Each is short and ends with a full stop."""
    found = dict(every_message())

    assert len(found) > 60
    for name, message in found.items():
        assert_is_a_sentence(message, name)


def test_the_sentences_that_name_a_figure_name_it_in_tokens() -> None:
    assert adjustment_limit_message(1000 * MICRO) == "One adjustment can add at most 1,000 tokens."
    assert code_value_message(5 * MICRO, 500 * MICRO) == "Enter a value from 5 to 500 tokens."
    assert code_value_message(MICRO, MICRO) == "Enter a value from 1 to 1 token."
    assert code_value_message(500_000, 1500 * MICRO) == "Enter a value from 0.5 to 1,500 tokens."
    for sentence in (
        adjustment_limit_message(1000 * MICRO),
        code_value_message(5 * MICRO, 500 * MICRO),
    ):
        assert_is_a_sentence(sentence)


# --- Every field of every request, through the application ------------------------------

REACHED: Final[str] = "The request passed its validation and reached a service."


class ReachedService:
    """Stands in for every service: whatever a route calls on it says so and does
    nothing. A request that gets this far passed its validation."""

    def __getattr__(self, name: str) -> Any:
        async def reached(*arguments: object, **named: object) -> None:
            raise ConflictError(REACHED)

        return reached


ADMIN: Final[AuthenticatedUser] = AuthenticatedUser(
    id=uuid7(),
    email="admin@ethz.ch",
    name="Ada Admin",
    username="ada",
    role=UserRole.ADMIN,
    status=UserStatus.ACTIVE,
)
"""Who every request of these tests comes from: an admin passes every guard."""


@pytest.fixture(scope="module")
def nowhere(base_settings: Settings) -> Settings:
    """Settings whose three stores are ports nothing listens on. No test here asks a
    store; if one ever did, it would be refused at once instead of reaching whatever
    runs on this machine."""
    return base_settings.model_copy(
        update={
            "postgres": base_settings.postgres.model_copy(
                update={"host": LOOPBACK, "port": closed_port()}
            ),
            "redis": base_settings.redis.model_copy(
                update={"host": LOOPBACK, "port": closed_port()}
            ),
            "scylla": base_settings.scylla.model_copy(
                update={"hosts": [LOOPBACK], "port": closed_port()}
            ),
        }
    )


@pytest.fixture(scope="module")
async def api(nowhere: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """The real application, called by an admin, with every service stood in for."""
    container = Container.build(nowhere, clock=FrozenClock())
    replaced = 0
    for name, value in list(vars(container).items()):
        if type(value).__module__.startswith("app.services."):
            setattr(container, name, ReachedService())
            replaced += 1
    assert replaced >= 20, "the services of the container"
    application_under_test = create_app(container)
    caller = Caller(
        identity=PortalIdentity(email=ADMIN.email, name=ADMIN.name, role=ADMIN.role), user=ADMIN
    )
    application_under_test.dependency_overrides[authenticate_request] = lambda: caller
    origin = nowhere.app.public_base_url
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application_under_test),
        base_url=origin,
        headers={"Origin": origin},
    ) as client:
        yield client
    await container.stop()


VALID: Final[dict[type[RequestModel], dict[str, Any]]] = {
    SaveProfileRequest: dict(PROFILE),
    ProposeMarketRequest: dict(PROPOSAL),
    TradeRequest: dict(BUY),
    QuoteQuery: dict(BUY),
    PageQuery: {},
    LeaderboardQuery: {},
    AdminUserListQuery: {},
    RedeemCodeRequest: {"code": "ABCD-EFGH-JKMN"},
    RedeemVoucherRequest: {"code": "ABCD-EFGH"},
    IssueVoucherRequest: {"product_id": "mate"},
    CreateCodeBatchRequest: {
        "label": "Erstsemestrigen-Apéro",
        "count": 5,
        "value_cr": "35",
        "expires_at": (TEST_NOW + timedelta(days=30)).isoformat(),
        "one_per_user": True,
    },
    RevokeCodesRequest: {},
    ApproveMarketRequest: {"tier": "DEFAULT"},
    RejectMarketRequest: {"reason": "Not a question anybody can check."},
    HaltMarketRequest: {"reason": "The date is being moved."},
    ResumeMarketRequest: {},
    CloseMarketRequest: {},
    ResolveMarketRequest: {"outcome": "YES", "evidence": "It was on the menu."},
    VoidMarketRequest: {"reason": "The question cannot be decided."},
    SuspendUserRequest: {"reason": "Asked for it."},
    ReinstateUserRequest: {"reason": "Sorted out."},
    SetUserRoleRequest: {"role": "BARISTA"},
    AdjustBalanceRequest: {"amount_cr": "5", "reason": "Won the quiz."},
}
"""A request each model takes: what a wrong value is put into, one field at a time."""

BASES_ONLY: Final[frozenset[type[RequestModel]]] = frozenset({TradeSize})
"""Request models no route takes by itself: what a quote and a trade have in common."""

WRONG_IN_A_BODY: Final[tuple[object, ...]] = (
    None,
    True,
    0,
    -1,
    1.5,
    10**20,
    "",
    "   ",
    "abc",
    "x" * 5001,
    "-1",
    "1.5",
    "150",
    "1e3",
    "0.0000001",
    "9" * 30,
    "anna-m",
    "Ünï cödé",
    "2026-02-30T25:00:00Z",
    "2026-10-16T12:00:00",
    [],
    ["a"],
    {},
    {"a": 1},
)
"""What a field of a JSON body is tried with: nothing, the wrong type, the wrong size,
the wrong characters, and what a person types into the wrong box."""

WRONG_IN_A_QUERY: Final[tuple[str, ...]] = (
    "",
    " ",
    "abc",
    "-1",
    "1.5",
    "150",
    "50%",
    "0.0000001",
    "1e3",
    "nan",
    "inf",
    "9" * 30,
    "x" * 5001,
    "true",
    "null",
    "anna-m",
)
"""What a query parameter is tried with. An address carries text and nothing else."""

PATH_VALUES: Final[dict[str, str]] = {
    "{market_id}": str(uuid7()),
    "{user_id}": str(uuid7()),
    "{batch_id}": str(uuid7()),
    "{voucher_id}": str(uuid7()),
    "{alert_id}": "1",
}


@dataclass(frozen=True)
class Use:
    """One place where a request model is taken: an operation, and its body or query."""

    method: str
    path: str
    where: str
    model: type[RequestModel]

    @property
    def url(self) -> str:
        url = self.path
        for placeholder, value in PATH_VALUES.items():
            url = url.replace(placeholder, value)
        assert "{" not in url, f"no test value for a parameter of {self.path}"
        return url


def request_models() -> set[type[RequestModel]]:
    """Every request model the application defines."""
    found: set[type[RequestModel]] = set()

    def collect(model: type[RequestModel]) -> None:
        for derived in model.__subclasses__():
            if derived.__module__.startswith("app."):
                found.add(derived)
                collect(derived)

    collect(RequestModel)
    return found


def uses() -> list[Use]:
    """Where the operations of the API take a request model, read from its document:
    the model a body refers to by name, and the model whose fields are exactly the
    query parameters of an operation."""
    by_name = {model.__name__: model for model in request_models()}
    document = create_app().openapi()
    found: list[Use] = []
    for path, item in sorted(document["paths"].items()):
        for method, operation in sorted(item.items()):
            body = operation.get("requestBody")
            if body is not None:
                name = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                found.append(Use(method.upper(), path, "body", by_name[name]))
            asked = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}
            fitting = [model for model in by_name.values() if set(model.model_fields) == asked]
            # A model and one derived from it without a field more have the same fields:
            # the operation takes the derived one.
            for model in sorted(fitting, key=lambda model: model.__name__):
                if asked and not any(
                    issubclass(other, model) for other in fitting if other != model
                ):
                    found.append(Use(method.upper(), path, "query", model))
    return found


USES: Final[list[Use]] = uses()

FIELDS: Final[list[tuple[Use, str]]] = [
    (use, field) for use in USES for field in use.model.model_fields
]


def test_every_request_model_is_taken_by_an_operation_and_has_a_request_to_start_from() -> None:
    """A request model added later is covered below without anyone adding it here: it
    shows up on its operation. What it needs is an entry in `VALID`."""
    used = {use.model for use in USES}

    assert request_models() - used == BASES_ONLY
    assert used == set(VALID)
    assert len(USES) >= 24, "five query models and nineteen bodies"
    assert len(FIELDS) >= 77
    assert {use.where for use in USES} == {"body", "query"}


async def send(api: httpx.AsyncClient, use: Use, payload: dict[str, Any]) -> httpx.Response:
    if use.where == "query":
        return await api.request(use.method, use.url, params=payload)
    return await api.request(
        use.method, use.url, json=payload, headers={"Idempotency-Key": str(uuid7())}
    )


def refusals(response: httpx.Response, sent: object) -> list[dict[str, Any]]:
    """The field errors of a 422, each held to be a sentence; none for a request that
    reached its service."""
    body = response.json()
    if response.status_code == 409 and body["message"] == REACHED:
        return []
    assert response.status_code == 422, f"{sent!r}: {response.status_code} {response.text[:300]}"
    assert body["code"] == "VALIDATION_ERROR", sent
    errors: list[dict[str, Any]] = body["details"]["errors"]
    assert errors, sent
    for error in errors:
        assert set(error) == {"loc", "msg", "type"}, sent
        assert_is_a_sentence(error["msg"], f"{error['loc']} for {str(sent)[:40]!r}")
        assert error["msg"] != NEUTRAL, f"{error['type']} has no sentence of its own: {sent!r}"
    return errors


@pytest.mark.parametrize(
    ("use", "field"),
    FIELDS,
    ids=[f"{use.method} {use.path} {use.where}.{field}" for use, field in FIELDS],
)
async def test_whatever_a_field_is_refused_with_is_a_sentence_for_the_person_who_typed_it(
    api: httpx.AsyncClient, use: Use, field: str
) -> None:
    """Typical wrong input for one field of a request that is right otherwise: nothing,
    a blank, a word where a number goes, a number where a choice goes, too much, too
    long, the wrong characters. Each is either taken, or refused with sentences and
    nothing that begins like the framework's own messages."""
    valid = VALID[use.model]
    assert refusals(await send(api, use, valid), "the request to start from") == []
    battery = WRONG_IN_A_QUERY if use.where == "query" else WRONG_IN_A_BODY
    refused_on_the_field = 0

    for wrong in battery:
        errors = refusals(await send(api, use, valid | {field: wrong}), wrong)
        refused_on_the_field += any(error["loc"][-1] == field for error in errors)

    assert refused_on_the_field, f"nothing of the battery is wrong for {field}"

    left_out = {name: value for name, value in valid.items() if name != field}
    if use.model.model_fields[field].is_required():
        (missing,) = refusals(await send(api, use, left_out), "left out")
        assert (missing["loc"], missing["type"]) == ([use.where, field], "missing")
        assert missing["msg"] == "This is required."
    else:
        # Taken, unless a rule about several fields needs it: that is a sentence too.
        refusals(await send(api, use, left_out), "left out")


@pytest.mark.parametrize("use", USES, ids=[f"{use.method} {use.path} {use.where}" for use in USES])
async def test_a_field_the_request_does_not_have_is_refused_with_a_sentence(
    api: httpx.AsyncClient, use: Use
) -> None:
    errors = refusals(await send(api, use, VALID[use.model] | {"surprise": "1"}), "an extra field")

    assert [(error["loc"], error["type"]) for error in errors] == [
        ([use.where, "surprise"], "extra_forbidden")
    ]
    assert errors[0]["msg"] == "This field is not part of the request."


BODIES: Final[list[Use]] = [use for use in USES if use.where == "body"]


@pytest.mark.parametrize("use", BODIES, ids=[f"{use.method} {use.path}" for use in BODIES])
async def test_a_body_that_is_no_json_object_is_refused_with_a_sentence(
    api: httpx.AsyncClient, use: Use
) -> None:
    headers = {"Content-Type": "application/json", "Idempotency-Key": str(uuid7())}
    answered = {
        sent: refusals(await api.request(use.method, use.url, content=sent, headers=headers), sent)
        for sent in (b"{not json", b"[]", b'"a word"', b"12", b"null")
    }

    for sent, errors in answered.items():
        if not errors:
            # `null` is no body at all, which a request without required fields may be.
            assert sent == b"null"
            assert not any(f.is_required() for f in use.model.model_fields.values())
            continue
        assert errors[0]["loc"][0] == "body", sent
    assert answered[b"{not json"][0]["msg"] == "The request is not valid JSON."
    assert answered[b"[]"][0]["msg"] == "Send the fields of the request as one JSON object."


async def test_what_is_wrong_in_the_address_or_a_header_is_answered_with_a_sentence(
    api: httpx.AsyncClient,
) -> None:
    """A path that names no id, a header that is missing or is no id, and the one query
    parameter that belongs to no model."""
    market = PATH_VALUES["{market_id}"]
    user = PATH_VALUES["{user_id}"]

    no_market = await api.get("/api/markets/not-an-id")
    no_alert = await api.post("/api/admin/alerts/first/ack")
    no_key = await api.post(f"/api/markets/{market}/trades", json=BUY)
    no_uuid = await api.post(
        f"/api/admin/users/{user}/adjustments",
        json=VALID[AdjustBalanceRequest],
        headers={"Idempotency-Key": "tap-1"},
    )
    no_status = await api.get("/api/markets", params={"status": "SOMEDAY"})

    assert refusals(no_market, "a path") == [
        {"loc": ["path", "market_id"], "msg": "This is not a valid id.", "type": "uuid_parsing"}
    ]
    assert refusals(no_alert, "a path") == [
        {"loc": ["path", "alert_id"], "msg": "Enter a whole number.", "type": "int_parsing"}
    ]
    assert refusals(no_key, "a header") == [
        {"loc": ["header", "Idempotency-Key"], "msg": "This is required.", "type": "missing"}
    ]
    assert refusals(no_uuid, "a header") == [
        {
            "loc": ["header", "Idempotency-Key"],
            "msg": "This is not a valid id.",
            "type": "uuid_parsing",
        }
    ]
    assert refusals(no_status, "a query") == [
        {"loc": ["query", "status", 0], "msg": "Choose one of the options.", "type": "enum"}
    ]


async def test_a_rule_about_several_fields_reaches_a_client_on_the_field_it_names(
    api: httpx.AsyncClient,
) -> None:
    """`loc` ends with the field a form marks, and `msg` is the sentence under it."""
    market = PATH_VALUES["{market_id}"]
    decided_first = PROPOSAL | {"resolve_by": (TEST_NOW + timedelta(days=6)).isoformat()}

    proposed = await api.post("/api/markets", json=decided_first)
    approved = await api.post(f"/api/admin/markets/{market}/approve", json={"tier": "CUSTOM"})
    quoted = await api.get(
        f"/api/markets/{market}/quote", params={"side": "SELL", "outcome": "YES", "amount_cr": "1"}
    )
    unsized = await api.get(f"/api/markets/{market}/quote", params={"side": "BUY", "outcome": "NO"})

    assert refusals(proposed, "a proposal") == [
        {"loc": ["body", "resolve_by"], "msg": DECIDED_BEFORE_CLOSE_MESSAGE, "type": "value_error"}
    ]
    assert refusals(approved, "an approval") == [
        {"loc": ["body", "budget_cr"], "msg": BUDGET_NEEDED_MESSAGE, "type": "value_error"}
    ]
    assert refusals(quoted, "a quote") == [
        {"loc": ["query", "amount_cr"], "msg": SELL_BY_SHARES_MESSAGE, "type": "value_error"}
    ]
    assert refusals(unsized, "a quote") == [
        {"loc": ["query"], "msg": SIZED_ONE_WAY_MESSAGE, "type": "value_error"}
    ]
