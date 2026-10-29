import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from shorts_engine.config import ConfigError, validate_config_files
from shorts_engine.db.base import engine as default_engine
from shorts_engine.db.base import get_data_dir

REQUIRED_TABLES = {
    "topics",
    "jobs",
    "job_events",
    "sources",
    "claims",
    "scripts",
    "assets",
    "segment_assets",
    "artifacts",
    "approvals",
    "publications",
    "cost_entries",
}
REQUIRED_MODULES = {
    "alembic": "alembic",
    "anthropic": "anthropic",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "sqlalchemy": "sqlalchemy",
    "tavily-python": "tavily",
    "PyYAML": "yaml",
    "faster-whisper": "faster_whisper",
    "onnxruntime": "onnxruntime",
    "numpy": "numpy",
    "num2words": "num2words",
    "tokenizers": "tokenizers",
    "huggingface-hub": "huggingface_hub",
}


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck]

    @property
    def ok(self) -> bool:
        return all(check.status != "FAIL" for check in self.checks)


def _tool_output(
    command: list[str],
    runner: Callable = subprocess.run,
) -> subprocess.CompletedProcess:
    try:
        return runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def collect_doctor_report(
    db_engine: Engine = default_engine,
    data_dir: Path | None = None,
    runner: Callable = subprocess.run,
    which: Callable = shutil.which,
    environ: dict[str, str] | None = None,
) -> DoctorReport:
    environment = environ if environ is not None else os.environ
    checks = []

    python_ok = sys.version_info >= (3, 12)
    checks.append(
        DoctorCheck(
            "Python",
            "OK" if python_ok else "FAIL",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; "
            "requires 3.12 or newer",
        )
    )

    missing_packages = []
    for distribution, module_name in REQUIRED_MODULES.items():
        try:
            module_spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError):
            module_spec = None
        try:
            distribution_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            distribution_version = None
        if module_spec is None and distribution_version is None:
            missing_packages.append(distribution)
    checks.append(
        DoctorCheck(
            "Python dependencies",
            "OK" if not missing_packages else "FAIL",
            "Installed" if not missing_packages else f"Missing: {', '.join(missing_packages)}",
        )
    )

    ffmpeg_path = which("ffmpeg")
    ffprobe_path = which("ffprobe")
    checks.append(
        DoctorCheck(
            "FFmpeg",
            "OK" if ffmpeg_path else "FAIL",
            ffmpeg_path or "Not found on PATH",
        )
    )
    checks.append(
        DoctorCheck(
            "FFprobe",
            "OK" if ffprobe_path else "FAIL",
            ffprobe_path or "Not found on PATH",
        )
    )

    ffmpeg_filters = ""
    ffmpeg_encoders = ""
    if ffmpeg_path:
        filters_result = _tool_output([ffmpeg_path, "-hide_banner", "-filters"], runner)
        encoders_result = _tool_output([ffmpeg_path, "-hide_banner", "-encoders"], runner)
        ffmpeg_filters = filters_result.stdout + filters_result.stderr
        ffmpeg_encoders = encoders_result.stdout + encoders_result.stderr

    has_libass = "subtitles" in ffmpeg_filters
    checks.append(
        DoctorCheck(
            "FFmpeg libass/subtitles",
            "OK" if has_libass else "FAIL" if ffmpeg_path else "WARN",
            "Subtitle filter available"
            if has_libass
            else "FFmpeg does not report the subtitles filter",
        )
    )

    nvenc_available = "h264_nvenc" in ffmpeg_encoders
    checks.append(
        DoctorCheck(
            "h264_nvenc",
            "OK" if nvenc_available else "WARN",
            "Encoder available"
            if nvenc_available
            else "Unavailable; preview must use the CPU fallback",
        )
    )

    nvidia_smi = which("nvidia-smi")
    if nvidia_smi:
        gpu_result = _tool_output(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader",
            ],
            runner,
        )
    else:
        gpu_result = None
    gpu_available = bool(gpu_result and gpu_result.returncode == 0 and gpu_result.stdout.strip())
    checks.append(
        DoctorCheck(
            "GPU/CUDA/VRAM",
            "OK" if gpu_available else "WARN",
            gpu_result.stdout.strip()
            if gpu_available and gpu_result
            else "NVIDIA GPU/CUDA not detected; CPU mode will be used",
        )
    )

    config_valid = True
    try:
        validate_config_files()
        config_detail = "Configuration files are valid"
    except ConfigError as exc:
        config_valid = False
        config_detail = str(exc)
    checks.append(
        DoctorCheck(
            "Configuration",
            "OK" if config_valid else "FAIL",
            config_detail,
        )
    )

    resolved_data_dir = data_dir or get_data_dir()
    data_accessible = resolved_data_dir.is_dir() and os.access(resolved_data_dir, os.R_OK | os.W_OK)
    checks.append(
        DoctorCheck(
            "Data directory",
            "OK" if data_accessible else "WARN",
            str(resolved_data_dir)
            if data_accessible
            else f"Not initialized or not writable: {resolved_data_dir}",
        )
    )

    try:
        with db_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            existing_tables = set(inspect(connection).get_table_names())
            missing_tables = REQUIRED_TABLES - existing_tables
            migration_exists = "alembic_version" in existing_tables
            revision = (
                connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
                if migration_exists
                else None
            )
        if missing_tables or revision is None:
            database_status = "FAIL"
            database_detail = (
                "Database is not fully migrated; run `shorts init` "
                f"(missing tables: {', '.join(sorted(missing_tables)) or 'revision'})"
            )
        else:
            database_status = "OK"
            database_detail = f"SQLite connected; migration revision {revision}"
    except SQLAlchemyError:
        database_status = "FAIL"
        database_detail = "SQLite is unavailable; run `shorts init` and check permissions"
    checks.append(DoctorCheck("SQLite/migrations", database_status, database_detail))

    configured_keys = [
        key
        for key in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "PIXABAY_API_KEY", "PEXELS_API_KEY")
        if environment.get(key)
    ]
    checks.append(
        DoctorCheck(
            "API key presence",
            "OK" if configured_keys else "WARN",
            "Configured: " + ", ".join(configured_keys)
            if configured_keys
            else "No paid providers configured; secrets are never displayed",
        )
    )

    voice_python = environment.get("VOICE_PYTHON", "")
    voice_ready = bool(voice_python) and Path(voice_python).is_file()
    checks.append(
        DoctorCheck(
            "Cloned voice",
            "OK" if voice_ready else "WARN",
            "Voice environment found"
            if voice_ready
            else "VOICE_PYTHON is not set or missing; recordings still work, --clone does not",
        )
    )

    return DoctorReport(checks)
