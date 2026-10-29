import itertools
import re
from typing import TypeVar

from pydantic import BaseModel

from shorts_engine.domain.schemas import (
    ClaimCandidate,
    ScriptSegment,
    TopicCandidate,
    TopicPolicyVerdict,
)

from .base import BaseLLMProvider

T = TypeVar("T", bound=BaseModel)

# Distinct subjects so the duplicate filter never rejects consecutive mock topics.
_MOCK_TOPICS = [
    ("Saturn's rings", "are slowly raining into the planet"),
    ("Olympus Mons", "is the tallest volcano known"),
    ("the Sun's corona", "is hotter than its surface"),
    ("Jupiter's Great Red Spot", "has been shrinking"),
    ("Enceladus", "sprays water into space"),
    ("Io", "has hundreds of active volcanoes"),
    ("Mars sunsets", "look blue"),
    ("Neptune's winds", "are the fastest in the solar system"),
    ("Titan", "has lakes of liquid methane"),
    ("Uranus", "spins on its side"),
    ("the Moon", "is drifting away from Earth"),
    ("Europa", "hides an ocean under ice"),
    ("Mercury", "has ice in its polar craters"),
    ("Phobos", "is slowly falling toward Mars"),
    ("Betelgeuse", "dimmed sharply in 2019"),
    ("the Orion Nebula", "is a star nursery"),
    ("Halley's Comet", "returns about every 76 years"),
    ("meteor showers", "come from comet dust"),
    ("the Andromeda Galaxy", "is approaching the Milky Way"),
    ("sunspots", "are cooler than the surface around them"),
    ("Callisto", "is covered in craters"),
    ("Venus's clouds", "are made of sulfuric acid"),
    ("Ceres", "has bright salt deposits"),
    ("the Kuiper Belt", "lies beyond Neptune"),
]
_MOCK_CATEGORIES = ["planets_and_moons", "stars_and_the_sun", "space_phenomena"]
_MOCK_SERIES = ["Strange Worlds", "Everyone Gets This Wrong", "Cosmic Scale"]
_mock_topic_counter = itertools.count()


class MockLLMProvider(BaseLLMProvider):
    def generate_structured(
        self, prompt: str, schema: type[T], model_name: str, effort: str | None = None
    ) -> T:
        if schema.__name__ == "ScriptOutput":
            return schema(
                title_working="Mock Title",
                hook="This is a mock hook.",
                hook_claim_ids=["claim_1"],
                segments=[
                    ScriptSegment(
                        text="This is a mock segment text.",
                        claim_ids=["claim_1"],
                        kind="factual",
                        visual_suggestion="A mock visual suggestion",
                    )
                ],
                ending="This is a mock ending.",
                ending_kind="framing",
                ending_claim_ids=[],
                estimated_duration_s=40,
                story_structure="Mock structure",
                series="Mock Series",
                episode_number=1,
            )
        elif schema.__name__ == "SearchQueries":
            return schema(queries=["mock scientific facts", "mock research study"])
        elif schema.__name__ == "ExtractedClaims":
            # Ekstrak dari src_mock (asumsi source_id pertama dari database,
            # tapi untuk mock kita bypass dengan id palsu, nanti dihandle di pipeline)
            return schema(
                claims=[
                    ClaimCandidate(
                        text="Mock factual claim",
                        supporting_quote="This is mock extracted text",
                        source_id="src_mock",
                        is_hook=True,
                    )
                ]
            )
        elif schema.__name__ == "CorroborationResult":
            return schema(found=False, supporting_quote="", reason="Mock: no corroboration")
        elif schema.__name__ == "ClaimSupportCheck":
            return schema(status="supports", reason="Mock verification passed")

        elif schema.__name__ == "TopicList":
            match = re.search(r"Propose exactly (\d+) new topics", prompt)
            count = int(match.group(1)) if match else 3
            topics = []
            for _ in range(count):
                n = next(_mock_topic_counter)
                subject, behavior = _MOCK_TOPICS[n % len(_MOCK_TOPICS)]
                topics.append(
                    TopicCandidate(
                        text=f"Why a {subject} {behavior} (mock {n})",
                        category=_MOCK_CATEGORIES[n % len(_MOCK_CATEGORIES)],
                        series=_MOCK_SERIES[n % len(_MOCK_SERIES)],
                        hook_idea=f"A {subject} does something unexpected.",
                        subject=subject,
                        specificity_score=8,
                        novelty_score=7,
                        visual_potential_score=7,
                        hook_surprise_score=6,
                        footage_queries=[subject, f"{subject} nature"],
                    )
                )
            return schema(topics=topics)
        elif schema.__name__ == "TopicPolicyCheck":
            indexes = [int(i) for i in re.findall(r"^\[(\d+)\]", prompt, flags=re.MULTILINE)]
            return schema(
                verdicts=[
                    TopicPolicyVerdict(index=i, violated_categories=[], reason="Mock: acceptable")
                    for i in indexes
                ]
            )

        return schema.model_construct()
