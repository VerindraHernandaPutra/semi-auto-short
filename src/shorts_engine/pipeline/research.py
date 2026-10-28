import os
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.config import get_config
from shorts_engine.db.models import Job, Source, Topic
from shorts_engine.domain.schemas import SearchQueries
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.providers.factory import get_llm_provider, get_search_provider


def run_research(db: Session, job: Job) -> bool:
    config = get_config()
    llm = get_llm_provider()
    search = get_search_provider()

    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    if not topic:
        transition_job_state(db, job, JobState.FAILED, reason="Topik tidak ditemukan")
        return False

    transition_job_state(db, job, JobState.RESEARCHING, reason="Mulai pencarian sumber")

    model_name = config.settings.get("models", {}).get("light", "claude-haiku-5-5")
    prompt = f"Buat 3-6 query pencarian web berbahasa Inggris untuk mencari fakta ilmiah mendalam tentang: {topic.text}"

    try:
        queries_result = llm.generate_structured(prompt, SearchQueries, model_name)
        queries = queries_result.queries
    except Exception as e:
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal generate query: {e}")
        return False

    search_results = search.search_and_extract(queries)

    if not search_results:
        transition_job_state(db, job, JobState.DROPPED, reason="insufficient_evidence")
        return False

    # Siapkan folder khusus pekerjaan ini untuk menyimpan hasil ekstrak (Sesuai bagian 6 Spesifikasi)
    job_dir = Path("data") / "jobs" / job.id / "research"
    os.makedirs(job_dir, exist_ok=True)

    for res in search_results:
        # Simpan isi konten ke file teks
        file_name = f"src_{uuid.uuid4().hex[:8]}.txt"
        file_path = job_dir / file_name
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(res["content"])

        new_source = Source(
            id=f"src_{uuid.uuid4().hex[:8]}",
            job_id=job.id,
            url=res["url"],
            domain=res["domain"],
            title=res["title"],
            tier=res["tier"],
            extracted_text_path=str(file_path),
        )
        db.add(new_source)

    db.commit()
    # Pindah status otomatis ke tahap selanjutnya
    transition_job_state(db, job, JobState.VERIFYING, reason="Riset selesai, lanjut verifikasi")
    return True
