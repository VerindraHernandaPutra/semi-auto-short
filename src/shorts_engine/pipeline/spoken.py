import re

from num2words import num2words

from shorts_engine.pipeline.script_rules import spoken_text

_ORDINAL = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b")
_GROUPED = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
_DECIMAL = re.compile(r"\b\d+\.\d+\b")
_RANGE = re.compile(r"\b(\d+)-(\d+)\b")
_INTEGER = re.compile(r"\b\d+\b")
_PERCENT = re.compile(r"(\d)\s?%")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Written abbreviations a voice would misread, and whose full stop is not the end of a sentence
# (a live run read "on Sept. 15, 2017" as "on Sept." and a separate "fifteen, twenty seventeen").
ABBREVIATIONS = {
    "Jan.": "January", "Feb.": "February", "Mar.": "March", "Apr.": "April", "Jun.": "June",
    "Jul.": "July", "Aug.": "August", "Sep.": "September", "Sept.": "September",
    "Oct.": "October", "Nov.": "November", "Dec.": "December",
    "Dr.": "Doctor", "Mt.": "Mount", "St.": "Saint", "approx.": "approximately",
    "vs.": "versus", "e.g.": "for example", "i.e.": "that is", "etc.": "and so on",
    "U.S.": "United States", "U.K.": "United Kingdom",
}  # fmt: skip
_ABBREVIATION = re.compile(
    r"(?<![A-Za-z.])("
    + "|".join(re.escape(key) for key in sorted(ABBREVIATIONS, key=len, reverse=True))
    + r")(?![A-Za-z])"
)
_MONTHS = (
    "January February March April May June July August September October November December"
).split()
_MONTH_DAY = re.compile(r"\b(" + "|".join(_MONTHS) + r") (\d{1,2})\b(?![\d,.]\d)")


def is_abbreviation(token: str) -> bool:
    """True for a written word whose full stop belongs to an abbreviation, not to a sentence."""
    return token in ABBREVIATIONS


# A four-digit number followed by one of these counts something; otherwise it is read as a year.
_NOT_A_YEAR_BEFORE = frozenset(
    "miles mile kilometers kilometres km meters metres feet foot degrees years days hours "
    "people times kg tons pounds light-years".split()
)


def _words(value: int | float, **kwargs) -> str:
    return num2words(value, lang="en", **kwargs)


def _integer(match: re.Match[str], text: str) -> str:
    number = int(match.group())
    next_word = re.match(r"\s+([A-Za-z-]+)", text[match.end() :])
    counts_something = bool(next_word and next_word.group(1).lower() in _NOT_A_YEAR_BEFORE)
    if 1100 <= number <= 2099 and not counts_something:
        return _words(number, to="year")
    return _words(number)


def to_spoken(text: str) -> str:
    """Text a speech model can read: digits become words, hints and brackets disappear.

    Years read in pairs ("eighteen thirty-one", "twenty sixteen"); numbers with a comma read as
    cardinals ("twenty-four thousand, two hundred"). The written script keeps its digits for the
    subtitles; only the audio sees this version.
    """
    text = spoken_text(text)
    text = _ABBREVIATION.sub(lambda m: ABBREVIATIONS[m.group(1)], text)
    text = _MONTH_DAY.sub(
        lambda m: (
            f"{m.group(1)} {_words(int(m.group(2)), to='ordinal')}"
            if 1 <= int(m.group(2)) <= 31
            else m.group(0)
        ),
        text,
    )
    text = _PERCENT.sub(r"\1 percent", text)
    text = _ORDINAL.sub(lambda m: _words(int(m.group(1)), to="ordinal"), text)
    text = _GROUPED.sub(lambda m: _words(int(m.group().replace(",", ""))), text)
    text = _DECIMAL.sub(lambda m: _words(float(m.group())), text)
    text = _RANGE.sub(lambda m: f"{_words(int(m.group(1)))} to {_words(int(m.group(2)))}", text)
    text = _INTEGER.sub(lambda m: _integer(m, text), text)
    return " ".join(text.replace("[", " ").replace("]", " ").split())


def to_sentences(parts: list[str]) -> list[str]:
    """One spoken sentence per item. Speech models stay steadier on short inputs."""
    sentences: list[str] = []
    for part in parts:
        spoken = to_spoken(part)
        sentences += [s.strip() for s in _SENTENCE_END.split(spoken) if s.strip()]
    return sentences


def spoken_words(part: str) -> list[tuple[str, list[str]]]:
    """Each written word of ``part`` with the words a speaker says for it.

    "24,200" becomes four spoken words; a plain word stays itself. The words around it are used
    as context, because "2000 miles" and "in 2000" are read differently, and so are "15" and
    "Sept. 15" ("fifteenth"). The result matches what ``to_spoken`` gives the voice.
    """
    tokens = spoken_text(part).split()
    pairs: list[tuple[str, list[str]]] = []
    for index, token in enumerate(tokens):
        previous = tokens[index - 1] if index > 0 else ""
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        together = to_spoken(f"{previous} {token} {following}").split()
        before = len(to_spoken(previous).split()) if previous else 0
        after = len(to_spoken(following).split()) if following else 0
        words = together[before : len(together) - after]
        if not words:  # the context changed how its neighbours are read; fall back to the token
            words = to_spoken(token).split()
        pairs.append((token, words))
    return pairs
