import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError

from shorts_engine.config import ConfigError, validate_config_files
from shorts_engine.db.base import (
    PROJECT_ROOT,
    SessionLocal,
    get_data_dir,
    get_database_path,
    get_database_url,
)
from shorts_engine.db.models import Job, JobEvent, Topic
from shorts_engine.doctor import collect_doctor_report
from shorts_engine.domain.state_machine import (
    InvalidStateTransition,
    JobState,
    transition_job_state,
)
from shorts_engine.logging_config import configure_logging
from shorts_engine.pipeline.audio import run_audio_alignment
from shorts_engine.pipeline.footage import run_footage_selection
from shorts_engine.pipeline.render import run_rendering
from shorts_engine.pipeline.research import run_research
from shorts_engine.pipeline.script import run_script
from shorts_engine.pipeline.topics import (
    STATUS_QUEUED,
    TopicGenerationError,
    refill_topic_queue,
)
from shorts_engine.pipeline.verify import run_verify

app = typer.Typer(help="Shorts Engine CLI - Produksi Video Otomatis")


def _migrate_to_head() -> None:
    get_database_path().parent.mkdir(parents=True, exist_ok=True)
    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))
    command.upgrade(alembic_config, "head")


def _get_job_or_exit(db, job_id: str) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        typer.echo(f"Job tidak ditemukan: {job_id}", err=True)
        raise typer.Exit(code=1)
    return job


@app.command()
def init():
    """Buat folder kerja dan siapkan database melalui migrasi."""
    data_dir = get_data_dir()
    for directory in (data_dir, data_dir / "jobs", data_dir / "backups", data_dir / "logs"):
        directory.mkdir(parents=True, exist_ok=True)

    try:
        validate_config_files()
        log_path = configure_logging(data_dir)
        _migrate_to_head()
    except (ConfigError, CommandError, OSError, SQLAlchemyError, RuntimeError, ValueError) as exc:
        typer.echo(f"Inisialisasi gagal: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Inisialisasi selesai. Database: {get_database_path()}")
    typer.echo(f"Log aplikasi: {log_path}")


@app.command()
def doctor():
    """Periksa Python, konfigurasi, database, FFmpeg, dan akselerasi media."""
    report = collect_doctor_report()
    for check in report.checks:
        typer.echo(f"[{check.status}] {check.name}: {check.detail}")
    typer.echo(
        "Mode render: "
        + (
            "GPU/NVENC tersedia"
            if any(check.name == "h264_nvenc" and check.status == "OK" for check in report.checks)
            else "CPU fallback"
        )
    )
    if not report.ok:
        raise typer.Exit(code=1)


@app.command(name="list")
def list_jobs(
    status: str | None = typer.Option(None, "--status", help="Filter berdasarkan status job"),
):
    """Tampilkan pekerjaan, dengan filter status opsional."""
    db = SessionLocal()
    try:
        query = db.query(Job).order_by(desc(Job.created_at))
        if status:
            query = query.filter(Job.status == status)
        jobs = query.limit(100).all()
        if not jobs:
            typer.echo("Tidak ada pekerjaan yang cocok.")
            return
        typer.echo("JOB ID | STATUS | TOPIC | CREATED")
        for job in jobs:
            topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
            topic_text = topic.text if topic else "(topik hilang)"
            typer.echo(
                f"{job.id} | {job.status} | {topic_text} | "
                f"{job.created_at.isoformat() if job.created_at else '-'}"
            )
    except SQLAlchemyError as exc:
        typer.echo(f"Gagal membaca daftar job: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        db.close()


@app.command()
def status(job_id: str = typer.Argument(..., help="ID pekerjaan")):
    """Tampilkan detail status dan riwayat transisi pekerjaan."""
    db = SessionLocal()
    try:
        job = _get_job_or_exit(db, job_id)
        topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
        typer.echo(f"Job: {job.id}")
        typer.echo(f"Status: {job.status}")
        typer.echo(f"Topik: {topic.text if topic else '(topik hilang)'}")
        typer.echo(f"Dibuat: {job.created_at.isoformat() if job.created_at else '-'}")
        typer.echo(f"Diubah: {job.updated_at.isoformat() if job.updated_at else '-'}")
        if job.last_valid_state:
            typer.echo(f"Status terakhir yang valid: {job.last_valid_state}")
        if job.error:
            typer.echo(f"Alasan: {job.error}")
        events = (
            db.query(JobEvent)
            .filter(JobEvent.job_id == job.id)
            .order_by(JobEvent.created_at, JobEvent.id)
            .all()
        )
        typer.echo("Riwayat:")
        for event in events:
            previous = event.from_state or "(awal)"
            typer.echo(f"  {previous} -> {event.to_state} [{event.actor}] {event.reason or ''}")
    except SQLAlchemyError as exc:
        typer.echo(f"Gagal membaca status job: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        db.close()


@app.command()
def cancel(job_id: str = typer.Argument(..., help="ID pekerjaan yang akan dibatalkan")):
    """Batalkan pekerjaan yang belum mencapai status terminal."""
    db = SessionLocal()
    try:
        job = _get_job_or_exit(db, job_id)
        transition_job_state(
            db,
            job,
            JobState.CANCELLED,
            actor="user",
            reason="Dibatalkan melalui CLI",
        )
        typer.echo(f"Job {job_id} dibatalkan.")
    except InvalidStateTransition as exc:
        typer.echo(f"Job tidak dapat dibatalkan: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except SQLAlchemyError as exc:
        typer.echo(f"Gagal membatalkan job: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        db.close()


@app.command()
def backup(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Lokasi file backup (default: data/backups dengan timestamp)",
    ),
):
    """Buat salinan konsisten SQLite menggunakan SQLite backup API."""
    source_path = get_database_path()
    if not source_path.is_file():
        typer.echo("Database belum ada. Jalankan `shorts init` terlebih dahulu.", err=True)
        raise typer.Exit(code=1)

    destination = output or (
        get_data_dir() / "backups" / f"shorts-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
    )
    destination = destination.expanduser().resolve()
    if destination == source_path.resolve():
        typer.echo("Lokasi backup tidak boleh sama dengan database sumber.", err=True)
        raise typer.Exit(code=1)
    if destination.exists():
        typer.echo(f"File backup sudah ada: {destination}", err=True)
        raise typer.Exit(code=1)

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source_path) as source_connection:
            with sqlite3.connect(destination) as backup_connection:
                source_connection.backup(backup_connection)
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        typer.echo(f"Backup database gagal: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Backup selesai: {destination}")


@app.command(name="topics")
def topics_command(
    refill: bool = typer.Option(False, "--refill", help="Isi ulang antrean topik"),
):
    db = SessionLocal()
    try:
        if refill:
            typer.echo("Menyiapkan topik baru...")
            try:
                report = refill_topic_queue(db)
            except TopicGenerationError as exc:
                typer.echo(f"Gagal mengisi antrean topik: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            typer.echo(
                f"Topik masuk antrean: {len(report.queued)}. Topik dibuang: {len(report.rejected)}."
            )
            for text, reason in report.rejected:
                typer.echo(f"  dibuang ({reason}): {text}")
        queued = (
            db.query(Topic).filter(Topic.status == STATUS_QUEUED).order_by(desc(Topic.score)).all()
        )
        typer.echo(f"Antrean Topik Saat Ini ({len(queued)}):")
        for topic in queued:
            typer.echo(f"  - [{topic.score}] [{topic.category}] {topic.text}")
    finally:
        db.close()


@app.command(name="produce")
def produce_command(
    count: int = typer.Option(1, "--count", min=1, help="Jumlah naskah yang ingin diproduksi"),
):
    """Jalankan pipeline dari topik sampai naskah siap rekam."""
    db = SessionLocal()
    try:
        for index in range(count):
            typer.echo(f"\n--- Memulai Produksi #{index + 1} ---")
            topic = (
                db.query(Topic)
                .filter(Topic.status == STATUS_QUEUED)
                .order_by(desc(Topic.score))
                .first()
            )
            if not topic:
                typer.echo(
                    "Antrean topik kosong. Jalankan `shorts topics --refill` terlebih dahulu."
                )
                break

            topic.status = "used"
            db.commit()

            job_id = f"job_{uuid.uuid4().hex[:8]}"
            job = Job(
                id=job_id,
                topic_id=topic.id,
                status=JobState.TOPIC_QUEUED.value,
            )
            db.add(job)
            db.commit()
            typer.echo(f"Topik terpilih: {topic.text} (Job ID: {job_id})")

            if run_research(db, job) and run_verify(db, job):
                if run_script(db, job):
                    typer.echo(f"Berhasil. Naskah siap direkam untuk Job {job_id}")
                else:
                    typer.echo(f"Gagal di tahap penulisan naskah. Status: {job.status}")
            else:
                typer.echo(f"Riset/verifikasi tidak lolos. Status: {job.status}")
    finally:
        db.close()


@app.command(name="process-audio")
def process_audio(
    job_id: str = typer.Argument(..., help="ID Job yang akan diproses"),
    audio_path: Path = typer.Argument(..., help="Path file audio (.mp3 atau .wav)"),
):
    """Memproses file audio rekaman dengan Whisper untuk alignment subtitle."""
    db = SessionLocal()
    try:
        job = _get_job_or_exit(db, job_id)
        if job.status != JobState.AWAITING_VOICE.value:
            typer.echo(
                f"Job {job_id} tidak menunggu audio (status: {job.status}).",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Memproses audio untuk Job: {job_id}...")
        if run_audio_alignment(db, job, str(audio_path)):
            typer.echo("Audio selesai diproses.")
        else:
            db.refresh(job)
            typer.echo(f"Gagal memproses audio. Detail: {job.error}", err=True)
            raise typer.Exit(code=1)
    finally:
        db.close()


@app.command(name="process-footage")
def process_footage(job_id: str = typer.Argument(..., help="ID Job yang akan diproses")):
    """Mencari dan mengunduh video background dari provider footage aktif."""
    db = SessionLocal()
    try:
        job = _get_job_or_exit(db, job_id)
        if job.status != JobState.SELECTING_FOOTAGE.value:
            typer.echo(
                f"Job {job_id} belum siap memilih footage (status: {job.status}).",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Memilih footage untuk Job: {job_id}...")
        if run_footage_selection(db, job):
            typer.echo("Footage berhasil diproses.")
        else:
            db.refresh(job)
            typer.echo(f"Gagal memproses footage. Detail: {job.error}", err=True)
            raise typer.Exit(code=1)
    finally:
        db.close()


@app.command(name="process-render")
def process_render(
    job_id: str = typer.Argument(..., help="ID Job yang akan dirender"),
    audio_path: Path = typer.Argument(..., help="Path file audio (.mp3 atau .wav)"),
):
    """Merender video akhir menggunakan FFmpeg."""
    db = SessionLocal()
    try:
        job = _get_job_or_exit(db, job_id)
        if job.status != JobState.RENDERING.value:
            typer.echo(
                f"Job {job_id} belum siap dirender (status: {job.status}).",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"Memulai rendering video untuk Job: {job_id}...")
        if run_rendering(db, job, str(audio_path)):
            typer.echo(f"Video selesai: data/jobs/{job_id}/final_video.mp4")
        else:
            db.refresh(job)
            typer.echo(f"Gagal merender. Detail: {job.error}", err=True)
            raise typer.Exit(code=1)
    finally:
        db.close()


@app.command(name="serve")
def serve_web(port: int = typer.Option(8000, min=1, max=65535, help="Port server web")):
    """Menjalankan Web API Server FastAPI di komputer lokal."""
    typer.echo(f"Menjalankan Shorts Engine di http://127.0.0.1:{port}")
    uvicorn.run("shorts_engine.api.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
