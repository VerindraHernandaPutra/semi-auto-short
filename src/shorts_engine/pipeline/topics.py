import uuid
from typing import List
from sqlalchemy.orm import Session
from shorts_engine.db.models import Topic
from shorts_engine.domain.schemas import TopicList
from shorts_engine.providers.factory import get_llm_provider
from shorts_engine.config import get_config
from shorts_engine.domain.schemas import TopicCandidate

def generate_topics(db: Session, count: int = 3) -> List[Topic]:
    config = get_config()
    llm = get_llm_provider()
    
    model_name = config.settings.get("models", {}).get("light", "claude-haiku-5-5")
    categories = ", ".join(config.topic_policy.get("active_categories", []))
    
    prompt = f"""
    Hasilkan {count} ide topik video pendek sains yang menarik untuk audiens dewasa dan remaja.
    Kategori yang diizinkan: {categories}.
    Fokus pada alam, hewan, laut, atau sains fenomena sehari-hari.
    """
    
    # Memanggil LLM untuk menghasilkan ide terstruktur
    result = llm.generate_structured(prompt, TopicList, model_name)
    
    saved_topics = []
    # Jika menggunakan mock (LLM tidak merespons topics), kita buat data tiruan manual
    if not hasattr(result, "topics") or not result.topics:
        mock_candidates = []
        for i in range(count):
            candidate = TopicCandidate(
                text=f"Bagaimana Gurita Mengubah Warna #{i+1}",
                category="animals",
                series="Strange Creatures",
                hook_idea="Pernahkah kamu melihat gurita berganti warna dalam sekejap?",
                visual_potential_score=9
            )
            mock_candidates.append(candidate)
        result.topics = mock_candidates

    for item in result.topics:
        new_topic = Topic(
            id=f"top_{uuid.uuid4().hex[:8]}",
            text=item.text,
            category=item.category,
            series=item.series,
            score=float(item.visual_potential_score),
            score_reason=item.hook_idea,
            status="candidate"
        )
        db.add(new_topic)
        saved_topics.append(new_topic)
        
    db.commit()
    return saved_topics