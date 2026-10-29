"""Deterministic script checks (SPEC 7.4). No model is involved, so they are cheap and exact."""

import math
import re
from typing import Any

HINT_PATTERN = re.compile(r"\[[^\]]*\]")  # pronunciation hints such as [sis-TEM-ik]
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
CAPITALISED = re.compile(r"[A-Z][A-Za-z'-]+")
WORD = re.compile(r"[A-Za-z0-9']+")
HYPHENATED = re.compile(r"[A-Za-z0-9']+(?:-[A-Za-z0-9']+)+")
SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)


def spoken_text(text: str) -> str:
    """Text as it will be read aloud: pronunciation hints are for the teleprompter only."""
    return " ".join(HINT_PATTERN.sub(" ", text).split())


def count_words(text: str) -> int:
    return len(WORD.findall(spoken_text(text)))


def word_budget(min_seconds: float, max_seconds: float, words_per_second: float) -> tuple[int, int]:
    """Word range that speaks within the target duration (SPEC 7.4: default 2.6 words/second)."""
    return math.ceil(min_seconds * words_per_second), math.floor(max_seconds * words_per_second)


def find_forbidden_phrases(text: str, phrases: list[str]) -> list[str]:
    lowered = spoken_text(text).casefold()
    return [
        phrase for phrase in phrases if re.search(rf"\b{re.escape(phrase.casefold())}\b", lowered)
    ]


def find_unclaimed_overstatements(statement: str, claims_text: str, words: list[str]) -> list[str]:
    """Strong words ("only", "always", superlatives) are allowed only when a claim says them."""
    spoken = spoken_text(statement).casefold()
    claimed = claims_text.casefold()
    found = []
    for word in words:
        pattern = rf"\b{re.escape(word.casefold())}\b"
        if re.search(pattern, spoken) and not re.search(pattern, claimed):
            found.append(word)
    return found


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def find_unsupported_names(statement: str, claims_text: str) -> list[str]:
    """Capitalised words inside a sentence (species, places, people) must appear in the claims.

    The first word of a sentence is ignored because it is capitalised anyway. This is a heuristic:
    it cannot see lower-case names, which the per-statement model check still covers.
    """
    # Hyphenated names ("D-ring") are matched whole as well as word by word.
    words = WORD.findall(claims_text) + HYPHENATED.findall(claims_text)
    claim_words = {_singular(w.casefold()) for w in words}
    unsupported = []
    for sentence in SENTENCE_SPLIT.split(spoken_text(statement)):
        for match in CAPITALISED.finditer(sentence):
            if match.start() == 0:
                continue
            name = match.group(0)
            if name == "I" or _singular(name.casefold()) in claim_words:
                continue
            if name not in unsupported:
                unsupported.append(name)
    return unsupported


def count_second_person(text: str) -> int:
    return len(SECOND_PERSON.findall(spoken_text(text)))


def length_problem(
    total_words: int, word_range: tuple[int, int], words_per_second: float
) -> str | None:
    low, high = word_range
    if low <= total_words <= high:
        return None
    seconds = total_words / words_per_second
    return (
        f"The script has {total_words} words, about {seconds:.0f} seconds when spoken. "
        f"It must have between {low} and {high} words in total."
    )


def describe(problems: list[Any], limit: int = 6) -> str:
    return "\n".join(f"- {problem}" for problem in problems[:limit])
