import os
import httpx
from typing import List, Dict, Any

class PexelsProvider:
    def __init__(self):
        self.api_key = os.getenv("PEXELS_API_KEY")
        self.base_url = "https://api.pexels.com/videos/search"
        
    def search_videos(self, query: str, per_page: int = 1) -> List[Dict[str, Any]]:
        # Jika tidak ada API key di .env, gunakan Mock Data
        if not self.api_key:
            return [{
                "video_files": [{"link": "mock_video.mp4", "quality": "hd"}],
            }]
            
        headers = {"Authorization": self.api_key}
        params = {"query": query, "per_page": per_page, "orientation": "portrait"}
        
        try:
            with httpx.Client() as client:
                response = client.get(self.base_url, headers=headers, params=params)
                response.raise_for_status()
                return response.json().get("videos", [])
        except Exception as e:
            print(f"Error mencari video di Pexels: {e}")
            return []