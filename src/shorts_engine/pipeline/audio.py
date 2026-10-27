import os
import json
from pathlib import Path
from sqlalchemy.orm import Session
from shorts_engine.db.models import Job
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.providers.factory import get_asr_provider

def run_audio_alignment(db: Session, job: Job, audio_file_path: str) -> bool:
    if not os.path.exists(audio_file_path):
        transition_job_state(db, job, JobState.FAILED, reason="File audio tidak ditemukan")
        return False
        
    transition_job_state(db, job, JobState.ALIGNING, reason="Mengekstrak timestamp kata dengan Whisper")
    
    try:
        result = get_asr_provider().transcribe(audio_file_path)
        
        words_data = []
        for segment in result.get("segments", []):
            for word in segment.get("words", []):
                words_data.append({
                    "word": word["word"],
                    "start": word["start"],
                    "end": word["end"]
                })
                
        # Simpan hasil alignment ke folder khusus pekerjaan ini
        job_dir = Path("data") / "jobs" / job.id / "audio"
        os.makedirs(job_dir, exist_ok=True)
        
        out_path = job_dir / "alignment.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(words_data, f, indent=2)
            
        transition_job_state(db, job, JobState.SELECTING_FOOTAGE, reason="Alignment kata selesai, lanjut pilih footage")
        return True
        
    except Exception as e:
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal memproses audio: {e}")
        return False