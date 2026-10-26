import os
import json
import httpx
from pathlib import Path
from sqlalchemy.orm import Session
from shorts_engine.db.models import Job, Script
from shorts_engine.providers.video.pexels import PexelsProvider
from shorts_engine.domain.state_machine import JobState, transition_job_state

def run_footage_selection(db: Session, job: Job) -> bool:
    script = db.query(Script).filter(Script.job_id == job.id).first()
    if not script:
        transition_job_state(db, job, JobState.FAILED, reason="Naskah tidak ditemukan")
        return False
        
    provider = PexelsProvider()
    job_dir = Path("data") / "jobs" / job.id / "footage"
    os.makedirs(job_dir, exist_ok=True)
    
    footage_plan = []
    
    for idx, segment in enumerate(script.segments):
        query = segment.get("visual_suggestion", "nature")
        videos = provider.search_videos(query, per_page=1)
        
        if videos and videos[0].get("video_files"):
            video_files = videos[0]["video_files"]
            # Cari resolusi HD atau gunakan yang pertama
            best_file = next((f for f in video_files if f.get("quality") == "hd"), video_files[0])
            download_url = best_file["link"]
            
            file_name = f"seg_{idx}.mp4"
            file_path = job_dir / file_name
            
            if download_url == "mock_video.mp4":
                import shutil
                source_video = Path("dummy_footage.mp4")
                # Jika video lokal ada, salin sebagai footage asli. Jika tidak, buat file dummy kosong.
                if source_video.exists():
                    shutil.copy(source_video, file_path)
                else:
                    with open(file_path, "w") as f:
                        f.write("mock video file")
            else:
                # Real: Unduh video asli dari Pexels
                try:
                    with httpx.Client(timeout=30.0) as client:
                        resp = client.get(download_url)
                        resp.raise_for_status()
                        with open(file_path, "wb") as f:
                            f.write(resp.content)
                except Exception as e:
                    print(f"Gagal mengunduh {download_url}: {e}")
                    continue
                    
            footage_plan.append({
                "segment_index": idx,
                "query": query,
                "local_path": str(file_path)
            })
            
    # Simpan rencana footage
    with open(job_dir / "footage_plan.json", "w", encoding="utf-8") as f:
        json.dump(footage_plan, f, indent=2)
        
    transition_job_state(db, job, JobState.RENDERING, reason="Footage siap, lanjut rendering")
    return True