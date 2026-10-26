from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class ScriptSegment(BaseModel):
    text: str = Field(..., description="Teks narasi untuk segmen ini")
    claim_ids: List[str] = Field(default_factory=list, description="Daftar ID klaim yang mendukung, kosongkan jika framing")
    kind: Literal["factual", "framing"] = Field("factual", description="Apakah segmen ini faktual atau sekadar transisi/framing")
    visual_suggestion: str = Field(..., description="Saran visual untuk pencarian footage")

class ScriptOutput(BaseModel):
    title_working: str = Field(..., description="Judul sementara")
    hook: str = Field(..., description="Kalimat pembuka (maks 2 detik)")
    hook_claim_ids: List[str] = Field(
        default_factory=list,
        description="Verified claim IDs that support every factual statement in the hook",
    )
    segments: List[ScriptSegment] = Field(..., description="Potongan naskah utama")
    ending: str = Field(..., description="Kalimat penutup yang tenang")
    ending_kind: Literal["factual", "framing"] = Field(
        "framing", description="Whether the ending contains a factual statement"
    )
    ending_claim_ids: List[str] = Field(
        default_factory=list,
        description="Verified claim IDs supporting a factual ending",
    )
    estimated_duration_s: int = Field(..., description="Estimasi durasi naskah dalam detik (35-55)")
    story_structure: str = Field(..., description="Struktur cerita dari panduan")
    series: str = Field(..., description="Nama seri")
    episode_number: Optional[int] = Field(None, description="Nomor episode jika ada")

class TopicCandidate(BaseModel):
    text: str = Field(..., description="Topik spesifik (misal: Bagaimana gurita mengubah warna)")
    category: str = Field(..., description="Kategori topik (misal: animals, ocean, everyday_phenomena)")
    series: str = Field(..., description="Nama seri yang cocok")
    hook_idea: str = Field(..., description="Ide kalimat pembuka")
    visual_potential_score: int = Field(..., description="Skor potensi visual 1-10")

class TopicList(BaseModel):
    topics: List[TopicCandidate]

class SearchQueries(BaseModel):
    queries: List[str] = Field(..., description="Daftar 3-6 query pencarian bahasa Inggris")

class ClaimCandidate(BaseModel):
    text: str = Field(..., description="Teks klaim faktual")
    supporting_quote: str = Field(..., description="Kutipan persis dari teks sumber")
    source_id: str = Field(..., description="ID sumber yang mendukung")
    is_hook: bool = Field(default=False, description="Apakah ini klaim utama untuk hook?")

class ExtractedClaims(BaseModel):
    claims: List[ClaimCandidate]

class ClaimSupportCheck(BaseModel):
    status: Literal["supports", "partial", "no"] = Field(..., description="Apakah kutipan mendukung klaim secara eksplisit?")
    reason: str = Field(..., description="Alasan singkat")


class ScriptClaimCheck(BaseModel):
    status: Literal["consistent", "overstated", "unsupported"] = Field(
        ..., description="Whether the script statement is supported by its claims"
    )
    reason: str = Field(..., description="Brief explanation of the assessment")