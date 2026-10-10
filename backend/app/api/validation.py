"""What a request that does not match its schema is told, field by field (SPEC §10, A.2).

A 422 lists one `{loc, msg, type}` per field that broke a rule, and a form shows `msg`
under that field as it is. The framework words such a message for a programmer ("String
should match pattern ..."); this module words it for the person who filled the field
in: a short sentence that says what to do and ends with a full stop.

One table, `SENTENCES`, makes the sentence from the rule that was broken (`type`) and
its figures (`ctx`: a length, a bound). It is asked for every request, so a rule reads
the same on every screen.

Where the rule alone cannot say it, the field does. An error raised with
`app.schemas.common.said` carries its sentence, and that one is used as it is: the
characters a username takes, a rate that is typed in percent, an amount with too many
places. A rule nobody wrote a sentence for is answered `NEUTRAL`, never with the
framework's wording.

`loc` and `type` pass unchanged. The rejected input is not echoed back.
"""

from collections.abc import Callable, Mapping
from typing import Any, Final

from app.schemas.common import SENTENCE

NEUTRAL: Final[str] = "This value is not valid."
"""The sentence for a rule the table does not know."""

type Context = Mapping[str, Any]
"""What the framework says about a broken rule besides its name: `min_length`, `le`."""


def _always(sentence: str) -> Callable[[Context], str]:
    return lambda context: sentence


def _number(value: object) -> str:
    """A bound as a sentence writes it: `1,000,000`, `0.5`, and `1` for 1.0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return format(value, ",.15g")
    return format(int(value), ",")


def _counted(count: object, one: str, several: str) -> str:
    """`1 character`, `2 characters`, `10,000 codes`."""
    return f"{_number(count)} {one if count == 1 else several}"


def _at_least_characters(context: Context) -> str:
    shortest = context["min_length"]
    if shortest == 1:
        return "This cannot be empty."
    return f"Use at least {_counted(shortest, 'character', 'characters')}."


def _at_most_characters(context: Context) -> str:
    return f"Use at most {_counted(context['max_length'], 'character', 'characters')}."


def _at_least_entries(context: Context) -> str:
    return f"Give at least {_counted(context['min_length'], 'entry', 'entries')}."


def _at_most_entries(context: Context) -> str:
    return f"Give at most {_counted(context['max_length'], 'entry', 'entries')}."


_REQUIRED: Final = _always("This is required.")
_WHOLE_NUMBER: Final = _always("Enter a whole number.")
_NUMBER: Final = _always("Enter a number.")
_ONE_OF_THE_OPTIONS: Final = _always("Choose one of the options.")
_YES_OR_NO: Final = _always("Choose yes or no.")
_TEXT: Final = _always("Enter text.")
_DATE_AND_TIME: Final = _always("Enter a date and a time.")
_AN_ID: Final = _always("This is not a valid id.")
_AN_OBJECT: Final = _always("Send the fields of the request as one JSON object.")

SENTENCES: Final[Mapping[str, Callable[[Context], str]]] = {
    # Something is there that should not be, or the other way round.
    "missing": _REQUIRED,
    "extra_forbidden": _always("This field is not part of the request."),
    "json_invalid": _always("The request is not valid JSON."),
    "model_attributes_type": _AN_OBJECT,
    "model_type": _AN_OBJECT,
    "dict_type": _AN_OBJECT,
    # Text.
    "string_type": _TEXT,
    "string_too_short": _at_least_characters,
    "string_too_long": _at_most_characters,
    "string_pattern_mismatch": _always("Use only the characters this field takes."),
    # Numbers.
    "int_type": _WHOLE_NUMBER,
    "int_parsing": _WHOLE_NUMBER,
    "int_from_float": _WHOLE_NUMBER,
    "int_parsing_size": _always("Enter a smaller number."),
    "float_type": _NUMBER,
    "float_parsing": _NUMBER,
    "finite_number": _NUMBER,
    "greater_than_equal": lambda context: f"Enter {_number(context['ge'])} or more.",
    "less_than_equal": lambda context: f"Enter {_number(context['le'])} or less.",
    "greater_than": lambda context: f"Enter more than {_number(context['gt'])}.",
    "less_than": lambda context: f"Enter less than {_number(context['lt'])}.",
    # A choice.
    "bool_type": _YES_OR_NO,
    "bool_parsing": _YES_OR_NO,
    "enum": _ONE_OF_THE_OPTIONS,
    "literal_error": _ONE_OF_THE_OPTIONS,
    # Ids and instants.
    "uuid_type": _AN_ID,
    "uuid_parsing": _AN_ID,
    "datetime_type": _DATE_AND_TIME,
    "datetime_parsing": _DATE_AND_TIME,
    "datetime_from_date_parsing": _DATE_AND_TIME,
    "datetime_object_invalid": _DATE_AND_TIME,
    "timezone_aware": _always("Enter a date and a time with its time zone."),
    # Lists.
    "list_type": _always("Give a list."),
    "too_short": _at_least_entries,
    "too_long": _at_most_entries,
}
"""From the framework's name of a broken rule to the sentence a person reads for it.
A name that is not here gets `NEUTRAL`: add a line when a request model starts using a
rule of another kind."""


def sentence_of(error: Mapping[str, Any]) -> str:
    """The `msg` of one validation error, as the framework reports it (`type`, `ctx`).

    The error's own sentence where it carries one (`said`), otherwise the table's for
    its `type`, otherwise `NEUTRAL`. A context that lacks the figure a sentence needs
    gets `NEUTRAL` too: the answer is never the framework's own wording.
    """
    context: Context = error.get("ctx") or {}
    own_words = context.get(SENTENCE)
    if isinstance(own_words, str):
        return own_words
    make = SENTENCES.get(error["type"])
    if make is None:
        return NEUTRAL
    try:
        return make(context)
    except KeyError:
        return NEUTRAL
