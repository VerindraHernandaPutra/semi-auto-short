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
    ("owl", "flies without making a sound"),
    ("hummingbird", "hovers in place"),
    ("firefly", "makes its own light"),
    ("camel", "survives long without water"),
    ("dolphin", "sleeps with half its brain"),
    ("spider", "builds a web overnight"),
    ("penguin", "stays warm in freezing wind"),
    ("chameleon", "shoots out its tongue"),
    ("bat", "finds insects in the dark"),
    ("jellyfish", "drifts without a brain"),
    ("rainbow", "forms after the rain"),
    ("ice cube", "cracks when it hits warm water"),
    ("thunder", "follows lightning"),
    ("soap bubble", "shows swirling colors"),
    ("shadow", "grows longer at sunset"),
    ("popcorn", "pops when heated"),
    ("sea turtle", "finds its birth beach"),
    ("starfish", "regrows a lost arm"),
    ("whale", "sings across the ocean"),
    ("crab", "walks sideways"),
    ("frost", "draws patterns on windows"),
    ("candle flame", "points upward"),
    ("ant", "carries many times its weight"),
    ("woodpecker", "hammers without a headache"),
]
_MOCK_CATEGORIES = ["animals", "ocean", "everyday_phenomena"]
_MOCK_SERIES = ["Strange Creatures", "Everyone Gets This Wrong", "Science at Home"]
_mock_topic_counter = itertools.count()


class MockLLMProvider(BaseLLMProvider):
    def generate_structured(
        self, prompt: str, schema: type[T], model_name: str, effort: str | None = None
    ) -> T:
        if schema.__name__ == "ScriptOutput":
            return schema(
                title_working="Mock Title",
                hook="This is a mock hook.",
                segments=[
                    ScriptSegment(
                        text="This is a mock segment text.",
                        claim_ids=["claim_1"],
                        kind="factual",
                        visual_suggestion="A mock visual suggestion",
                    )
                ],
                ending="This is a mock ending.",
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
