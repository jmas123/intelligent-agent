"""TOML config file loading for ~/.deadline-agent/config.toml."""

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".deadline-agent" / "config.toml"

# Map TOML sections to flat setting names
SECTION_MAP: dict[str, dict[str, str]] = {
    "core": {
        "log_level": "log_level",
        "database_url": "database_url",
        "owner_name": "owner_name",
    },
    "ai": {
        "ollama_base_url": "ollama_base_url",
        "anthropic_api_key": "anthropic_api_key",
        "anthropic_model": "anthropic_model",
        "use_anthropic_fallback": "use_anthropic_fallback",
        "embedding_model": "embedding_model",
        "extraction_model": "extraction_model",
    },
    "google": {
        "credentials_path": "google_credentials_path",
        "token_path": "google_token_path",
        "pubsub_topic": "gmail_pubsub_topic",
        "watch_renew_days": "gmail_watch_renew_days",
    },
    "moodle": {
        "ical_urls": "moodle_ical_urls",
        "fetch_interval_minutes": "moodle_fetch_interval_minutes",
    },
    "notifications": {
        "enable": "enable_notifications",
        "digest_time": "digest_time",
        "alert_check_interval_minutes": "alert_check_interval_minutes",
    },
    "dedup": {
        "similarity_threshold": "similarity_threshold",
        "window_days": "dedup_window_days",
    },
    "ui": {
        "menubar_refresh_seconds": "menubar_refresh_seconds",
    },
    "actions": {
        "auto_execute": "action_auto_execute",
        "expiry_hours": "action_expiry_hours",
    },
    "reasoning": {
        "interval_minutes": "reasoning_interval_minutes",
        "model": "reasoning_model",
        "provider": "reasoning_provider",
    },
    "watcher": {
        "recruiting_patterns": "recruiting_patterns",
        "project_directories": "project_directories",
        "directories": "watch_directories",
        "extensions": "watch_extensions",
        "ignore_patterns": "watch_ignore_patterns",
        "no_work_alert_hours": "no_work_alert_hours",
        "file_link_threshold": "file_link_threshold",
        "enable": "enable_file_watcher",
    },
    "behavioral": {
        "session_gap_minutes": "session_gap_minutes",
        "analysis_interval_hours": "behavioral_analysis_interval_hours",
        "min_pattern_confidence": "min_pattern_confidence",
    },
    "lora": {
        "log_training_data": "lora_log_training_data",
        "extraction_model": "lora_extraction_model",
        "reasoning_model": "lora_reasoning_model",
    },
    "identity": {
        "synthesis_enabled": "identity_synthesis_enabled",
        "export_path": "identity_export_path",
    },
    "social": {
        "enable_tone_analysis": "enable_tone_analysis",
        "enable_social_graph": "enable_social_graph",
    },
    "life_contexts": {
        "contexts": "life_contexts",
    },
}


def _expand_value(value: Any) -> Any:
    """Expand environment variables and ~ in string values."""
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [_expand_value(item) for item in value]
    return value


def load_config_file(path: Path | None = None) -> dict[str, Any]:
    """Load and flatten a TOML config file into a dict of setting names.

    Returns empty dict if file doesn't exist.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        logger.exception("Failed to parse config file: %s", config_path)
        return {}

    flat: dict[str, Any] = {}
    for section, mapping in SECTION_MAP.items():
        section_data = raw.get(section, {})
        for toml_key, setting_name in mapping.items():
            if toml_key in section_data:
                flat[setting_name] = _expand_value(section_data[toml_key])

    logger.debug("Loaded %d settings from %s", len(flat), config_path)
    return flat
