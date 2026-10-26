import re
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def quote_similarity(source_text: str, quote: str) -> float:
    normalized_source = normalize_text(source_text)
    normalized_quote = normalize_text(quote)
    if not normalized_source or not normalized_quote:
        return 0.0
    if normalized_quote in normalized_source:
        return 1.0

    source_words = normalized_source.split()
    quote_words = normalized_quote.split()
    if not source_words or not quote_words:
        return 0.0

    max_delta = max(1, len(quote_words) // 10)
    min_size = max(1, len(quote_words) - max_delta)
    max_size = len(quote_words) + max_delta
    best_score = 0.0
    for size in range(min_size, max_size + 1):
        for start in range(max(0, len(source_words) - size + 1)):
            candidate = " ".join(source_words[start : start + size])
            score = SequenceMatcher(None, normalized_quote, candidate).ratio()
            best_score = max(best_score, score)

    return best_score


_NUMBER_WITH_UNIT = re.compile(
    r"""
    (?<!\w)
    (?P<number>
        \d+(?:[,.]\d+)*
        |zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve
        |dozen|hundred|thousand|million|billion
    )
    \s*
    (?P<unit>
        %|°\s?[cf]
        |kilometers?|km|meters?|metres?|miles?|mi|m
        |centimeters?|cm|millimeters?|mm|inches?|in|feet|ft|yards?|yd
        |seconds?|secs?|s|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|yrs?
        |grams?|g|kilograms?|kg|pounds?|lbs?|tons?|liters?|litres?|l|milliliters?|ml
        |celsius|fahrenheit|kelvin
    )?
    (?!\w)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def numeric_facts(text: str) -> set[tuple[str, str]]:
    facts = set()
    for match in _NUMBER_WITH_UNIT.finditer(text):
        number = match.group("number").replace(",", "")
        unit = (match.group("unit") or "").casefold().replace(" ", "")
        facts.add((number, unit))
    return facts


def numeric_facts_are_supported(claim: str, quote: str) -> bool:
    return numeric_facts(claim).issubset(numeric_facts(quote))


def claim_key(claim: str) -> str:
    return normalize_text(claim)
