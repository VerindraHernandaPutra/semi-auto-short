import uuid
import typer
import uvicorn
from sqlalchemy import text
from shorts_engine.db.base import SessionLocal
from shorts_engine.db.models import Topic, Job
from shorts_engine.domain.state_machine import JobState, transition_job_state

from shorts_engine.pipeline.topics import generate_topics
from shorts_engine.pipeline.research import run_research
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.pipeline.script import run_script
from shorts_engine.pipeline.audio import run_audio_alignment
from shorts_engine.pipeline.footage import run_footage_selection
from shorts_engine.pipeline.render import run_rendering

app = typer.Typer(help="Shorts Engine CLI - Produksi Video Otomatis")

@app.command()
def doctor():
    typer.echo("Memeriksa status Shorts Engine...")
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        typer.echo("✓ Database SQLite terhubung dengan baik.")
    except Exception as e:
        typer.echo(f"✗ Gagal menghubungkan ke database: {e}")
    finally:
        db.close()

@app.command(name="topics")
def topics_command(refill: bool = typer.Option(False, "--refill", help="Isi ulang antrean topik")):
    db = SessionLocal()
    try:
        if refill:
            typer.echo("Menghasilkan topik baru...")
            new_topics = generate_topics(db, count=3)
            typer.echo(f"✓ Berhasil menambahkan {len(new_topics)} topik baru.")
        else:
            typer.echo("Antrean Topik Saat Ini:")
            topics_in_db = db.query(Topic).filter(Topic.status == "candidate").all()
            for t in topics_in_db:
                typer.echo(f"  - [{t.category}] {t.text}")
    finally:
        db.close()

@app.command(name="produce")
def produce_command(count: int = typer.Option(1, "--count", help="Jumlah naskah yang ingin diproduksi")):
    """Jalankan pipeline dari topik sampai naskah siap rekam."""
    db = SessionLocal()
    try:
        for i in range(count):
            typer.echo(f"\n--- Memulai Produksi #{i+1} ---")
            
            # Cari topik kandidat di antrean
            topic = db.query(Topic).filter(Topic.status == "candidate").first()
            if not topic:
                typer.echo("Antrean topik kosong. Silakan jalankan `shorts topics --refill` terlebih dahulu.")
                break
                
            # Tandai topik sedang digunakan
            topic.status = "used"
            db.commit()
            
            # Buat Job baru
            job_id = f"job_{uuid.uuid4().hex[:8]}"
            job = Job(id=job_id, topic_id=topic.id, status=JobState.TOPIC_QUEUED.value)
            db.add(job)
            db.commit()
            
            typer.echo(f"Topik terpilih: {topic.text} (Job ID: {job_id})")
            
            # Eksekusi Pipeline secara berurutan
            if run_research(db, job):
                typer.echo("✓ Riset selesai.")
                if run_verify(db, job):
                    typer.echo("✓ Verifikasi fakta selesai.")
                    if run_script(db, job):
                        typer.echo(f"✓ BERHASIL! Naskah siap direkam untuk Job {job_id}")
                    else:
                        typer.echo(f"✗ Gagal di tahap Penulisan Naskah. Status: {job.status}")
                else:
                    typer.echo(f"✗ Gagal di tahap Verifikasi (Topik dibuang). Status: {job.status}")
            else:
                typer.echo(f"✗ Gagal di tahap Riset. Status: {job.status}")
                
    finally:
        db.close()

@app.command(name="process-audio")
def process_audio(
    job_id: str = typer.Argument(..., help="ID Job yang akan diproses"),
    audio_path: str = typer.Argument(..., help="Path file audio (.mp3 atau .wav)")
):
    """Memproses file audio rekaman dengan Whisper untuk alignment subtitle."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            typer.echo(f"✗ Job dengan ID {job_id} tidak ditemukan.")
            return

        # KITA KOMENTARI SEMENTARA STATUS CHECK-NYA AGAR BISA RETRY JOB YANG 'FAILED'
        # if job.status != JobState.AWAITING_VOICE.value:
        #     typer.echo(f"✗ Job {job_id} tidak dalam status menunggu suara (Status saat ini: {job.status}).")
        #     return
            
        typer.echo(f"🎙️ Memproses audio untuk Job: {job_id}...")
        
        if run_audio_alignment(db, job, audio_path):
            typer.echo("✓ BERHASIL! Audio selesai diproses dan timestamp kata telah disimpan.")
        else:
            # Ambil data terbaru dari database untuk melihat error log-nya
            db.refresh(job)
            typer.echo(f"✗ Gagal memproses audio. Detail Error: {job.error}")
    finally:
        db.close()

@app.command(name="process-footage")
def process_footage(job_id: str = typer.Argument(..., help="ID Job yang akan diproses")):
    """Mencari dan mengunduh video background dari Pexels."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            typer.echo(f"✗ Job dengan ID {job_id} tidak ditemukan.")
            return

        typer.echo(f"🎬 Memilih dan mengunduh footage untuk Job: {job_id}...")
        
        if run_footage_selection(db, job):
            typer.echo("✓ BERHASIL! Footage berhasil dipilih dan diunduh (Mock/Real).")
        else:
            db.refresh(job)
            typer.echo(f"✗ Gagal memproses footage. Detail: {job.error}")
    finally:
        db.close()

@app.command(name="process-render")
def process_render(
    job_id: str = typer.Argument(..., help="ID Job yang akan dirender"),
    audio_path: str = typer.Argument(..., help="Path file audio (.mp3 atau .wav)")
):
    """Merender video akhir (audio + footage) menggunakan FFmpeg."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            typer.echo(f"✗ Job dengan ID {job_id} tidak ditemukan.")
            return

        typer.echo(f"🎞️ Memulai rendering video untuk Job: {job_id}...")
        
        if run_rendering(db, job, audio_path):
            typer.echo(f"✓ BERHASIL! Video akhir telah jadi dan disimpan di data/jobs/{job_id}/final_video.mp4")
        else:
            db.refresh(job)
            typer.echo(f"✗ Gagal merender video. Detail: {job.error}")
    finally:
        db.close()

@app.command(name="serve")
def serve_web(port: int = typer.Option(8000, help="Port untuk server web")):
    """Menjalankan Web API Server FastAPI."""
    typer.echo(f"🚀 Menjalankan Web Server Shorts Engine di http://localhost:{port}")
    uvicorn.run("shorts_engine.api.main:app", host="0.0.0.0", port=port, reload=True)

if __name__ == "__main__":
    app()