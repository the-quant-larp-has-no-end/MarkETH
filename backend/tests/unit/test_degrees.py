"""The degree catalogue (`app.core.degrees`): what a profile may name as its degree.

A label is what `users.degree_programme` stores and what every screen shows, so these
tests pin what would otherwise go wrong silently: a level without an entry, a
programme lost or added by accident, a label twice, a label the column cannot hold.
"""

from typing import Final

import pytest

from app.core.degrees import CATALOGUE, PROGRAMME_MAX_LENGTH, DegreeLevelEntry, is_programme
from app.core.enums import DegreeLevel

# ETH Zurich's programmes as ethz.ch listed them on 9 October 2026, and three entries
# of MarkETH's own for people who are in none of them.
ENTRIES_PER_LEVEL: Final[dict[DegreeLevel, int]] = {
    DegreeLevel.BACHELOR: 24,
    DegreeLevel.MASTER: 51,
    DegreeLevel.DOCTORATE: 16,
    DegreeLevel.CONTINUING_EDUCATION: 82,
    DegreeLevel.TEACHER_EDUCATION: 16,
    DegreeLevel.OTHER: 3,
}

LEVEL_NAMES: Final[dict[DegreeLevel, str]] = {
    DegreeLevel.BACHELOR: "Bachelor",
    DegreeLevel.MASTER: "Master",
    DegreeLevel.DOCTORATE: "Doctorate",
    DegreeLevel.CONTINUING_EDUCATION: "Continuing education",
    DegreeLevel.TEACHER_EDUCATION: "Teacher education",
    DegreeLevel.OTHER: "Other",
}

ALL_LABELS: Final[list[str]] = [label for entry in CATALOGUE for label in entry.programmes]


def test_there_are_six_levels() -> None:
    assert [level.value for level in DegreeLevel] == [
        "BACHELOR",
        "MASTER",
        "DOCTORATE",
        "CONTINUING_EDUCATION",
        "TEACHER_EDUCATION",
        "OTHER",
    ]


def test_the_catalogue_has_one_entry_per_level_in_the_order_of_the_enum() -> None:
    assert [entry.level for entry in CATALOGUE] == list(DegreeLevel)


def test_every_level_is_shown_under_its_name() -> None:
    assert {entry.level: entry.name for entry in CATALOGUE} == LEVEL_NAMES


def test_every_level_holds_its_number_of_programmes() -> None:
    assert {entry.level: len(entry.programmes) for entry in CATALOGUE} == ENTRIES_PER_LEVEL
    assert len(ALL_LABELS) == 192


def test_the_other_level_is_for_eth_members_in_no_programme() -> None:
    (other,) = [entry for entry in CATALOGUE if entry.level is DegreeLevel.OTHER]

    assert other.programmes == ("Exchange student", "ETH staff", "Other")


def test_no_label_appears_twice() -> None:
    """Not within a level, and not across levels either: a programme named without
    its level still means one thing. Case does not tell two labels apart."""
    assert len(set(ALL_LABELS)) == len(ALL_LABELS)
    assert len({label.casefold() for label in ALL_LABELS}) == len(ALL_LABELS)


def test_every_label_fits_the_column_and_is_written_cleanly() -> None:
    for label in ALL_LABELS:
        assert 0 < len(label) <= PROGRAMME_MAX_LENGTH, label
        assert label == " ".join(label.split()), f"stray whitespace in {label!r}"
        assert label.isprintable(), label


@pytest.mark.parametrize("entry", CATALOGUE, ids=[entry.level.value for entry in CATALOGUE])
def test_every_label_is_a_programme_of_its_own_level_and_of_no_other(
    entry: DegreeLevelEntry,
) -> None:
    for label in entry.programmes:
        assert is_programme(entry.level, label)
        for other in DegreeLevel:
            if other is not entry.level:
                assert not is_programme(other, label), (other, label)


@pytest.mark.parametrize(
    ("level", "programme"),
    [
        (DegreeLevel.BACHELOR, "BSc Computer Science"),
        (DegreeLevel.MASTER, "MSc Robotics, Systems and Control"),
        (DegreeLevel.DOCTORATE, "Doctorate Physics"),
        (DegreeLevel.CONTINUING_EDUCATION, "Executive MBA ETH HSG"),
        (DegreeLevel.TEACHER_EDUCATION, "Teaching Diploma Sport"),
        (DegreeLevel.OTHER, "ETH staff"),
    ],
)
def test_a_pair_of_the_catalogue_is_a_programme(level: DegreeLevel, programme: str) -> None:
    assert is_programme(level, programme)


@pytest.mark.parametrize(
    ("level", "programme"),
    [
        (DegreeLevel.MASTER, "BSc Computer Science"),
        (DegreeLevel.BACHELOR, "bsc computer science"),
        (DegreeLevel.BACHELOR, "BSc Computer Science "),
        (DegreeLevel.BACHELOR, "Computer Science"),
        (DegreeLevel.BACHELOR, "BSc Underwater Basket Weaving"),
        (DegreeLevel.OTHER, ""),
    ],
    ids=["other-level", "other-case", "padded", "without-title", "unknown", "empty"],
)
def test_anything_else_is_not(level: DegreeLevel, programme: str) -> None:
    """A label is taken character for character: it is a value, not a search term."""
    assert not is_programme(level, programme)
