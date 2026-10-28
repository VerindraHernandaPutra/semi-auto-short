import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_VARIABLE = "SHORTS_ENV_FILE"


def load_environment() -> Path | None:
    """Load secrets from a local .env file without overriding real environment variables.

    ``SHORTS_ENV_FILE`` selects another file; an empty value disables loading (used by tests
    so a developer's real keys never leak into the default test run). Values are never logged.
    """
    configured = os.environ.get(ENV_FILE_VARIABLE)
    if configured is not None and not configured.strip():
        return None
    env_path = Path(configured).expanduser() if configured else PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    load_dotenv(env_path, override=False)
    return env_path
