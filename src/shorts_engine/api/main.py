import os
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from shorts_engine.db.base import SessionLocal
from shorts_engine.db.models import Job, Topic
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.audio import run_audio_alignment
from shorts_engine.pipeline.footage import run_footage_selection
from shorts_engine.pipeline.render import run_rendering
from shorts_engine.pipeline.research import run_research
from shorts_engine.pipeline.script import run_script

# Import Pipeline
from shorts_engine.pipeline.topics import STATUS_QUEUED, generate_topics
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers.factory import set_active_providers

app = FastAPI(title="Shorts Engine Web UI", version="2.1")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- FUNGSI BACKGROUND TASKS ---
def background_produce():
    """Fungsi yang berjalan di belakang layar untuk memproduksi naskah dari awal."""
    db = SessionLocal()
    try:
        topic = (
            db.query(Topic)
            .filter(Topic.status == STATUS_QUEUED)
            .order_by(desc(Topic.score))
            .first()
        )
        if not topic:
            # Generate topik baru jika kosong
            topics = generate_topics(db, count=1)
            if not topics:
                return
            topic = topics[0]

        topic.status = "used"
        db.commit()

        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job = Job(id=job_id, topic_id=topic.id, status=JobState.TOPIC_QUEUED.value)
        db.add(job)
        db.commit()

        if run_research(db, job):
            if run_verify(db, job):
                run_script(db, job)
    finally:
        db.close()


def background_audio_render(job_id: str, audio_path: str):
    """Berjalan di belakang layar untuk menyelaraskan audio, mencari video, dan merender."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job and job.status == JobState.AWAITING_VOICE.value:
            if run_audio_alignment(db, job, audio_path):
                if run_footage_selection(db, job):
                    run_rendering(db, job, audio_path)
    finally:
        db.close()


# --- ENDPOINTS API ---
@app.get("/api/jobs")
def get_jobs(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(desc(Job.created_at)).limit(15).all()
    return jobs


@app.post("/api/produce")
def produce_video(background_tasks: BackgroundTasks):
    """Memicu pembuatan naskah baru di latar belakang."""
    background_tasks.add_task(background_produce)
    return {"message": "Produksi naskah dimulai di belakang layar!"}


@app.post("/api/jobs/{job_id}/upload-audio")
async def upload_audio(
    job_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Menerima file rekaman suara dari pengguna dan memulai proses rendering."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or job.status != JobState.AWAITING_VOICE.value:
        raise HTTPException(
            status_code=400, detail="Job tidak ditemukan atau tidak sedang menunggu audio"
        )

    # Simpan file yang diupload
    job_dir = Path("data") / "jobs" / job_id
    os.makedirs(job_dir, exist_ok=True)
    audio_path = job_dir / "user_voice.wav"

    with open(audio_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Jalankan sisa proses di latar belakang
    background_tasks.add_task(background_audio_render, job_id, str(audio_path))
    return {"message": "Audio diterima! Proses rendering sedang berjalan di belakang layar."}


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str):
    video_path = Path("data") / "jobs" / job_id / "final_video.mp4"
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video belum selesai/tidak ditemukan")
    return FileResponse(str(video_path), media_type="video/mp4")


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job or job.status != JobState.PENDING_REVIEW.value:
        raise HTTPException(status_code=400, detail="Video tidak bisa di-approve")

    transition_job_state(db, job, JobState.APPROVED, reason="Disetujui pengguna via Web")
    return {"message": "Video disetujui!"}


class ProviderSettings(BaseModel):
    llm: Literal["mock", "ollama", "anthropic"]
    search: Literal["mock", "tavily"]


@app.get("/api/settings/providers")
def get_providers():
    from shorts_engine.providers.factory import active_provider_names

    names = active_provider_names()
    return {"llm": names["llm"], "search": names["search"]}


@app.post("/api/settings/providers")
def update_providers(settings: ProviderSettings):
    set_active_providers(settings.llm, settings.search)
    return {"message": "Provider AI berhasil diperbarui!"}


# Keep the catch-all static mount after API routes so it does not shadow them.
app.mount("/", StaticFiles(directory="src/shorts_engine/api/static", html=True), name="static")
