import logging
import sqlite3
from importlib.machinery import ModuleSpec
from types import SimpleNamespace

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

from shorts_engine import config as config_module
from shorts_engine import doctor as doctor_module
from shorts_engine import logging_config
from shorts_engine.cli import app
from shorts_engine.db import models  # noqa: F401
from shorts_engine.db.base import Base
from shorts_engine.doctor import DoctorCheck, DoctorReport

runner = CliRunner()


def test_project_configuration_loads_and_validates():
    config = config_module.validate_config_files()

    assert config.settings["content_language"] == "en"
    assert config.topic_policy["active_categories"]
    assert config.source_whitelist["tier_a"]


def test_style_guide_is_loaded_from_config_dir():
    assert "Persona" in config_module.load_style_guide()


def test_empty_style_guide_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    (tmp_path / "style_guide.md").write_text("  \n", encoding="utf-8")

    with pytest.raises(config_module.ConfigError, match="empty"):
        config_module.load_style_guide()


def test_invalid_configuration_fails_with_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    (tmp_path / "settings.yaml").write_text("[]", encoding="utf-8")

    with pytest.raises(config_module.ConfigError, match="YAML mapping"):
        config_module.load_yaml("settings.yaml")


def test_configuration_rejects_invalid_verification_threshold():
    config_data = {
        "settings": {
            "content_language": "en",
            "models": {"heavy": "heavy-model", "light": "light-model"},
            "verification": {
                "min_verified_claims": 3,
                "quote_similarity_threshold": 1.5,
            },
            "speech": {
                "target_duration_min": 35,
                "target_duration_max": 55,
                "words_per_second": 2.6,
            },
        },
        "source_whitelist": {},
        "topic_policy": {
            "active_categories": [],
            "series": [],
            "forbidden_topics": [],
        },
    }

    with pytest.raises(ValueError, match="between 0 and 1"):
        config_module.AppConfig.model_validate(config_data)


def test_logging_configuration_uses_rotating_utf8_file(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    try:
        log_path = logging_config.configure_logging(tmp_path)

        assert log_path.parent == tmp_path / "logs"
        assert log_path.name == "shorts-engine.log"
        handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, logging_config._HANDLER_MARKER, False)
        ]
        assert handlers
        assert handlers[0].maxBytes == 5 * 1024 * 1024
        assert handlers[0].backupCount == 5
        assert handlers[0].encoding == "utf-8"
    finally:
        for handler in list(root_logger.handlers):
            if getattr(handler, logging_config._HANDLER_MARKER, False):
                root_logger.removeHandler(handler)
                handler.close()
        root_logger.setLevel(previous_level)


def test_cli_init_runs_migration_without_touching_other_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "application-data"))

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    database_path = tmp_path / "application-data" / "shorts.db"
    assert database_path.is_file()
    assert (tmp_path / "application-data" / "logs" / "shorts-engine.log").is_file()
    with create_engine(f"sqlite:///{database_path.as_posix()}").connect() as connection:
        table_names = set(inspect(connection).get_table_names())
    assert doctor_module.REQUIRED_TABLES <= table_names


def test_database_migration_is_additive(tmp_path, monkeypatch):
    import shorts_engine.cli as cli_module

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "migration-data"))
    cli_module._migrate_to_head()
    migration_config = Config(str(cli_module.PROJECT_ROOT / "alembic.ini"))
    alembic_command.downgrade(migration_config, "2e0946029b95")
    database_path = cli_module.get_database_path()
    with create_engine(f"sqlite:///{database_path.as_posix()}").begin() as connection:
        connection.execute(
            text(
                "INSERT INTO topics (id, text, category, status) "
                "VALUES ('keep', 'Existing topic', 'animals', 'candidate')"
            )
        )
    cli_module._migrate_to_head()

    with create_engine(f"sqlite:///{database_path.as_posix()}").connect() as connection:
        existing_topic = connection.execute(
            text("SELECT text FROM topics WHERE id='keep'")
        ).scalar_one()
        table_names = set(inspect(connection).get_table_names())
    assert existing_topic == "Existing topic"
    assert doctor_module.REQUIRED_TABLES <= table_names


def test_cli_backup_uses_sqlite_backup_api(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    destination = tmp_path / "backups" / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")

    import shorts_engine.cli as cli_module

    monkeypatch.setattr(cli_module, "get_database_path", lambda: source)
    monkeypatch.setattr(cli_module, "get_data_dir", lambda: tmp_path)

    result = runner.invoke(app, ["backup", "--output", str(destination)])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "preserved"


def test_doctor_reports_missing_media_dependencies_as_failures():
    checks = [
        DoctorCheck("FFmpeg", "FAIL", "Not found"),
        DoctorCheck("GPU/CUDA/VRAM", "WARN", "CPU fallback"),
    ]

    report = DoctorReport(checks)

    assert report.ok is False


def test_doctor_passes_for_cpu_mode_when_required_media_tools_exist(tmp_path, monkeypatch):
    database = tmp_path / "doctor.db"
    doctor_engine = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(doctor_engine)
    with doctor_engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('phase1-revision')"))

    monkeypatch.setattr(
        doctor_module.importlib.util,
        "find_spec",
        lambda module_name: ModuleSpec(module_name, loader=None),
    )
    monkeypatch.setattr(
        doctor_module.importlib.metadata,
        "version",
        lambda distribution: "1.0",
    )
    tool_paths = {
        "ffmpeg": "ffmpeg.exe",
        "ffprobe": "ffprobe.exe",
        "nvidia-smi": None,
    }
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: tool_paths[name])

    def fake_run(command, **kwargs):
        if command[-1] == "-filters":
            return SimpleNamespace(returncode=0, stdout=" ... subtitles ...", stderr="")
        if command[-1] == "-encoders":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {command}")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report = doctor_module.collect_doctor_report(
        db_engine=doctor_engine,
        data_dir=data_dir,
        runner=fake_run,
        which=lambda name: tool_paths[name],
        environ={},
    )

    assert report.ok
    assert next(check for check in report.checks if check.name == "GPU/CUDA/VRAM").status == "WARN"
    assert (
        next(check for check in report.checks if check.name == "FFmpeg libass/subtitles").status
        == "OK"
    )


def test_real_providers_are_the_default_and_dry_run_switches_all_to_mock():
    from shorts_engine.providers import factory

    assert factory.active_provider_names() == {
        "llm": "anthropic",
        "search": "tavily",
        "footage": "pixabay",
        "asr": "whisper",
    }

    factory.use_mock_providers()

    assert set(factory.active_provider_names().values()) == {"mock"}


def test_unknown_provider_in_settings_fails_loudly(monkeypatch):
    from shorts_engine.providers import factory

    config = config_module.get_config()
    config.settings["providers"] = {**config.settings["providers"], "llm": "gpt-nine"}
    monkeypatch.setattr(factory, "get_config", lambda: config)

    with pytest.raises(ValueError, match="llm"):
        factory.active_provider_names()


def test_topics_dry_run_uses_fakes_and_a_throwaway_database_not_the_real_one(monkeypatch):
    import shorts_engine.cli as cli_module

    def real_database_must_not_be_opened():
        raise AssertionError("dry-run opened the real database")

    monkeypatch.setattr(cli_module, "SessionLocal", real_database_must_not_be_opened)

    result = runner.invoke(app, ["topics", "--refill", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "--dry-run" in result.output
    assert "Topik masuk antrean:" in result.output
    assert "mock" in result.output  # fake topics were produced
