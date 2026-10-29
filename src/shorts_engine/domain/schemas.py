from typing import Literal

from pydantic import BaseModel, Field

# Field descriptions are sent to the model as part of the output schema, so they stay in
# English: the model mirrors their language, and video content must follow content_language.


class ScriptSegment(BaseModel):
    text: str = Field(..., description="Narration text for this segment")
    claim_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the verified claims backing this segment; empty for framing",
    )
    kind: Literal["factual", "framing"] = Field(
        ...,
        description=(
            "'factual' if the segment states any fact, 'framing' if it is only an opener, "
            "transition or closing line. Always set it explicitly."
        ),
    )
    visual_suggestion: str = Field(..., description="Visual idea used to search for stock footage")


class ScriptOutput(BaseModel):
    title_working: str = Field(..., description="Working title")
    hook: str = Field(..., description="Opening sentence (about 2 seconds when spoken)")
    hook_claim_ids: list[str] = Field(
        default_factory=list,
        description="Verified claim IDs that support every factual statement in the hook",
    )
    segments: list[ScriptSegment] = Field(..., description="Main body segments of the script")
    ending: str = Field(..., description="Calm closing line")
    ending_kind: Literal["factual", "framing"] = Field(
        ..., description="'factual' if the ending states a fact, otherwise 'framing'"
    )
    ending_claim_ids: list[str] = Field(
        default_factory=list,
        description="Verified claim IDs supporting a factual ending",
    )
    estimated_duration_s: int = Field(
        ..., description="Estimated spoken duration of the script in seconds (35-55)"
    )
    story_structure: str = Field(..., description="Story structure taken from the style guide")
    series: str = Field(..., description="Series name")
    episode_number: int | None = Field(None, description="Episode number, if known")


class TopicCandidate(BaseModel):
    text: str = Field(
        ..., description="Specific topic, e.g. 'How an octopus changes color in a split second'"
    )
    category: str = Field(..., description="One of the allowed topic categories")
    series: str = Field(..., description="Name of the series that fits the topic")
    hook_idea: str = Field(..., description="Idea for the opening sentence")
    visual_potential_score: int = Field(
        ..., description="How well common stock video could illustrate it, from 1 to 10"
    )
    subject: str = Field(
        ...,
        description=(
            "The specific named subject: a particular species, object or phenomenon, "
            "e.g. 'mantis shrimp'. Never a broad word such as 'bird' or 'ocean'."
        ),
    )
    specificity_score: int = Field(
        ...,
        description="How narrow and concrete the topic is, from 1 (broad trivia) to 10. Be critical.",
    )
    novelty_score: int = Field(
        ...,
        description="How rarely popular science videos cover it, from 1 to 10. Be critical.",
    )
    hook_surprise_score: int = Field(
        ..., description="How surprising and curiosity-provoking the hook is, from 1 to 10"
    )
    footage_queries: list[str] = Field(
        ...,
        description="2 to 3 short English stock-footage search queries, always in English",
    )


class TopicList(BaseModel):
    topics: list[TopicCandidate]


class TopicPolicyVerdict(BaseModel):
    index: int = Field(..., description="Index of the candidate topic being judged")
    violated_categories: list[str] = Field(
        default_factory=list,
        description="Forbidden categories the topic falls under; empty if it is acceptable",
    )
    reason: str = Field(..., description="Brief reason")


class TopicPolicyCheck(BaseModel):
    verdicts: list[TopicPolicyVerdict]


class SearchQueries(BaseModel):
    queries: list[str] = Field(..., description="3 to 6 English web search queries")


class ClaimCandidate(BaseModel):
    text: str = Field(..., description="Text of the factual claim")
    supporting_quote: str = Field(..., description="Exact quote copied from the source text")
    source_id: str = Field(..., description="ID of the source that supports the claim")
    is_hook: bool = Field(default=False, description="Whether this is the main claim for the hook")


class ExtractedClaims(BaseModel):
    claims: list[ClaimCandidate]


class ClaimSupportCheck(BaseModel):
    status: Literal["supports", "partial", "no"] = Field(
        ..., description="Does the quote explicitly support the claim?"
    )
    reason: str = Field(..., description="Brief reason")


class CorroborationResult(BaseModel):
    found: bool = Field(..., description="Whether the source text explicitly states the claim")
    supporting_quote: str = Field(
        default="",
        description="Exact quote copied from the source text that states the claim; empty if none",
    )
    reason: str = Field(..., description="Brief reason")


class ScriptClaimCheck(BaseModel):
    status: Literal["consistent", "overstated", "unsupported"] = Field(
        ..., description="Whether the script statement is supported by its claims"
    )
    reason: str = Field(..., description="Brief explanation of the assessment")
