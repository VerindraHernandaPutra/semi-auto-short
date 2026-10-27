from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
EXPECTED_CONFIG_FILES = (
    "settings.yaml",
    "source_whitelist.yaml",
    "topic_policy.yaml",
)


class ConfigError(ValueError):
    """Raised when a required project configuration file is invalid."""


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    settings: dict[str, Any]
    source_whitelist: dict[str, list[str]]
    topic_policy: dict[str, Any]

    @field_validator("settings")
    @classmethod
    def validate_settings(cls, settings: dict[str, Any]):
        if not isinstance(settings.get("content_language"), str) or not settings[
            "content_language"
        ].strip():
            raise ValueError("content_language must be a non-empty string")

        models = settings.get("models")
        if not isinstance(models, dict) or any(
            not isinstance(models.get(name), str) or not models[name].strip()
            for name in ("heavy", "light")
        ):
            raise ValueError("models must contain non-empty heavy and light names")

        verification = settings.get("verification")
        if not isinstance(verification, dict):
            raise ValueError("verification must be a mapping")
        minimum_claims = verification.get("min_verified_claims")
        quote_threshold = verification.get("quote_similarity_threshold")
        if not isinstance(minimum_claims, int) or minimum_claims < 1:
            raise ValueError("verification.min_verified_claims must be a positive integer")
        if (
            not isinstance(quote_threshold, (int, float))
            or not 0 <= quote_threshold <= 1
        ):
            raise ValueError(
                "verification.quote_similarity_threshold must be between 0 and 1"
            )

        speech = settings.get("speech")
        if not isinstance(speech, dict):
            raise ValueError("speech must be a mapping")
        duration_min = speech.get("target_duration_min")
        duration_max = speech.get("target_duration_max")
        words_per_second = speech.get("words_per_second")
        if (
            not isinstance(duration_min, (int, float))
            or not isinstance(duration_max, (int, float))
            or duration_min <= 0
            or duration_max < duration_min
        ):
            raise ValueError("speech duration range is invalid")
        if not isinstance(words_per_second, (int, float)) or words_per_second <= 0:
            raise ValueError("speech.words_per_second must be greater than zero")
        return settings

    @field_validator("source_whitelist")
    @classmethod
    def validate_source_whitelist(cls, whitelist: dict[str, list[str]]):
        for tier in ("tier_a", "tier_b", "tier_c"):
            domains = whitelist.get(tier)
            if domains is not None and (
                not isinstance(domains, list)
                or any(
                    not isinstance(domain, str) or not domain.strip()
                    for domain in domains
                )
            ):
                raise ValueError(f"{tier} must be a list of non-empty domains")
        return whitelist

    @field_validator("topic_policy")
    @classmethod
    def validate_topic_policy(cls, policy: dict[str, Any]):
        if not isinstance(policy.get("active_categories"), list):
            raise ValueError("active_categories must be a list")
        if not isinstance(policy.get("series"), list):
            raise ValueError("series must be a list")
        if not isinstance(policy.get("forbidden_topics"), list):
            raise ValueError("forbidden_topics must be a list")
        return policy


def load_yaml(file_name: str) -> dict[str, Any]:
    file_path = CONFIG_DIR / file_name
    if not file_path.is_file():
        raise ConfigError(f"Required configuration file is missing: {file_path}")

    try:
        with file_path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"Unable to read configuration file {file_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file must contain a YAML mapping: {file_path}")
    return data


def get_config() -> AppConfig:
    config_data = {
        "settings": load_yaml("settings.yaml"),
        "source_whitelist": load_yaml("source_whitelist.yaml"),
        "topic_policy": load_yaml("topic_policy.yaml"),
    }
    try:
        return AppConfig.model_validate(config_data)
    except ValidationError as exc:
        raise ConfigError(f"Project configuration is invalid: {exc}") from exc


def validate_config_files() -> AppConfig:
    missing = [
        name for name in EXPECTED_CONFIG_FILES if not (CONFIG_DIR / name).is_file()
    ]
    if missing:
        raise ConfigError(f"Missing configuration files: {', '.join(missing)}")
    return get_config()
