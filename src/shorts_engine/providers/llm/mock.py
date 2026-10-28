from typing import TypeVar

from pydantic import BaseModel

from shorts_engine.domain.schemas import ClaimCandidate, ScriptSegment

from .base import BaseLLMProvider

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider(BaseLLMProvider):
    def generate_structured(self, prompt: str, schema: type[T], model_name: str) -> T:
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

        return schema.model_construct()
