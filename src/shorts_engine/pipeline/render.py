import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.db.models import Job
from shorts_engine.domain.state_machine import JobState, transition_job_state


def run_rendering(db: Session, job: Job, audio_path: str) -> bool:
    job_dir = Path("data") / "jobs" / job.id
    footage_dir = job_dir / "footage"
    out_path = job_dir / "final_video.mp4"

    # Ambil video pertama dari rencana footage
    video_path = footage_dir / "seg_0.mp4"

    if not os.path.exists(audio_path):
        transition_job_state(db, job, JobState.FAILED, reason="File audio tidak ditemukan")
        return False

    try:
        # Cek apakah video adalah mock (ukuran file sangat kecil karena hanya teks)
        is_mock = False
        if os.path.exists(video_path) and os.path.getsize(video_path) < 100:
            is_mock = True

        if is_mock:
            # Jika mock, buat video latar biru dengan resolusi potret (720x1280)
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=720x1280",  # Video buatan FFmpeg
                "-i",
                audio_path,  # Audio asli Anda
                "-c:v",
                "libx264",
                "-tune",
                "stillimage",
                "-c:a",
                "aac",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",  # Potong video saat audio habis
                str(out_path),
            ]
        else:
            # Jika video asli (Real Pexels footage), gabungkan secara langsung
            cmd = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(video_path),
                "-i",
                audio_path,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
                str(out_path),
            ]

        # Jalankan FFmpeg di belakang layar
        subprocess.run(cmd, check=True, capture_output=True)

        transition_job_state(
            db, job, JobState.PENDING_REVIEW, reason="Rendering selesai, menunggu review manusia"
        )
        return True

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        transition_job_state(db, job, JobState.FAILED, reason=f"FFmpeg Error: {err}")
        return False
    except Exception as e:
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal merender: {e}")
        return False
