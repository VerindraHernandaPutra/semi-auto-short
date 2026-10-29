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
