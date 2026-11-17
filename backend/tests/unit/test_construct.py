"""Labels fixed by construction (design §3 L2, methodology §2): each way of corrupting a passing
answer is deterministic for a seed and states the label it earns under the grounded rubric."""

import random

import pytest

from lite_eval.judges.construct import (
    CONSTRUCTIONS,
    append_section,
    change_number,
    drop_citations,
    insert_sentence,
    swap_answer,
)

BASE = "Use 1 gram of coffee to 16 grams of water [1]."


def test_change_number_alters_exactly_one_number_and_is_unsupported() -> None:
    made = change_number(BASE, random.Random(7))
    assert made is not None
    assert made.label == "unsupported" and made.construction == "change_number"
    before = [w for w in BASE.split() if any(c.isdigit() for c in w)]
    after = [w for w in made.answer.split() if any(c.isdigit() for c in w)]
    assert (
        len(before) == len(after) and sum(a != b for a, b in zip(before, after, strict=True)) == 1
    )
    assert "[1]" in made.answer  # a citation marker is not a figure
    assert change_number(BASE, random.Random(7)) == made  # the same seed, the same case
    assert change_number("No figures here [1].", random.Random(7)) is None
    # in a list of citations, the middle number has a space before it and a comma after it
    assert change_number("No figures here [1, 3, 5].", random.Random(7)) is None


def test_the_other_constructions_and_their_labels() -> None:
    others = ["Rinse the paper filter first [1].", BASE]
    swapped = swap_answer(BASE, others, random.Random(1))
    assert swapped is not None and swapped.answer == others[0] and swapped.label == "unsupported"
    assert swap_answer(BASE, [BASE], random.Random(1)) is None  # nothing else to swap in

    inserted = insert_sentence(BASE, "The grinder must be cleaned weekly.")
    assert inserted.answer == BASE + " The grinder must be cleaned weekly."
    assert inserted.label == "unsupported"

    appended = append_section(BASE, "History: coffee came from Ethiopia.")
    assert appended.answer == BASE + "\n\nHistory: coffee came from Ethiopia."
    assert appended.label == "unsupported"

    # Citation markers are not judged by the grounded rubric: the content stays supported
    dropped = drop_citations(BASE)
    assert dropped.answer == "Use 1 gram of coffee to 16 grams of water."
    assert dropped.label == "grounded"


def test_every_construction_is_listed_with_its_label() -> None:
    assert CONSTRUCTIONS == {
        "change_number": "unsupported",
        "swap_answer": "unsupported",
        "insert_sentence": "unsupported",
        "append_section": "unsupported",
        "drop_citations": "grounded",
    }


@pytest.mark.parametrize("seed", range(20))
def test_a_changed_number_is_always_different(seed: int) -> None:
    made = change_number("It takes 0 minutes and 9.5 grams [2].", random.Random(seed))
    assert made is not None and made.answer != "It takes 0 minutes and 9.5 grams [2]."
