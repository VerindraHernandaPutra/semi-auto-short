from pathlib import Path
import yaml
from pydantic import BaseModel
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"

def load_yaml(file_name: str) -> Dict[str, Any]:
    file_path = CONFIG_DIR / file_name
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class AppConfig(BaseModel):
    settings: Dict[str, Any]
    source_whitelist: Dict[str, Any]
    topic_policy: Dict[str, Any]

def get_config() -> AppConfig:
    return AppConfig(
        settings=load_yaml("settings.yaml"),
        source_whitelist=load_yaml("source_whitelist.yaml"),
        topic_policy=load_yaml("topic_policy.yaml")
    )