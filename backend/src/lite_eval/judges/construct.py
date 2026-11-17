"""Labels fixed by construction (design §3 L2, methodology §2): take an answer that passes and
change it in one known way; the right verdict is known by construction. Each function states the
label its result earns under the grounded rubric (`CONSTRUCTIONS`). Choices are made with the
caller's `random.Random`, so a seed gives the same calibration set every time.

Construction inherits the base answers' shape (L2): also run a new rubric on real outputs and
look at the flag rate before trusting it.
"""

import random
import re
from dataclasses import dataclass

CONSTRUCTIONS: dict[str, str] = {
    "change_number": "unsupported",  # a figure no source states
    "swap_answer": "unsupported",  # another question's answer
    "insert_sentence": "unsupported",  # a statement from outside the sources
    "append_section": "unsupported",  # material nobody asked for, not in the sources
    "drop_citations": "grounded",  # citation markers are not judged: the content is unchanged
}

# [1], [1, 3], and the full-width separators Chinese answers use
_CITATION = re.compile(r"\s*\[\d+(?:\s*[,\uff0c\u3001]\s*\d+)*\]")
_NUMBER = re.compile(r"(?<![\w.\[])\d+(?:\.\d+)?(?![\w\]])")


@dataclass(frozen=True)
class Constructed:
    answer: str
    construction: str

    @property
    def label(self) -> str:
        return CONSTRUCTIONS[self.construction]


def change_number(answer: str, rng: random.Random) -> Constructed | None:
    """One figure, chosen by `rng`, changed to another value; None when there is none. Citation
    markers (`[1]`) are not figures."""
    spans = [m.span() for m in _NUMBER.finditer(_CITATION.sub(lambda m: " " * len(m[0]), answer))]
    if not spans:
        return None
    start, end = rng.choice(spans)
    old = answer[start:end]
    if "." in old:
        new = f"{float(old) + rng.choice((1.5, 2.5, 3.5)):.{len(old.split('.')[1])}f}"
    else:
        new = str(int(old) + rng.randint(1, 9))
    return Constructed(answer[:start] + new + answer[end:], "change_number")


def swap_answer(answer: str, others: list[str], rng: random.Random) -> Constructed | None:
    """Another question's answer in place of this one; None when every other is the same."""
    choices = [o for o in others if o != answer]
    return Constructed(rng.choice(choices), "swap_answer") if choices else None


def insert_sentence(answer: str, sentence: str) -> Constructed:
    """`sentence` (from a source this question was not given) added after the answer."""
    return Constructed(f"{answer} {sentence}", "insert_sentence")


def append_section(answer: str, section: str) -> Constructed:
    return Constructed(f"{answer}\n\n{section}", "append_section")


def drop_citations(answer: str) -> Constructed:
    return Constructed(_CITATION.sub("", answer), "drop_citations")
