"""Application configuration via environment variables and config.toml."""

from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from deadline_agent.config_loader import load_config_file


class TomlConfigSource(PydanticBaseSettingsSource):
    """Pydantic settings source that reads from ~/.deadline-agent/config.toml."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        toml_data = load_config_file()
        val = toml_data.get(field_name)
        return val, field_name, val is not None

    def __call__(self) -> dict[str, Any]:
        return load_config_file()


class Settings(BaseSettings):
    """Settings loaded from env vars, .env file, and config.toml.

    Precedence: direct env var > .env file > config.toml > defaults.
    """

    owner_name: str = ""  # e.g. "JudeElMasri" — used to parse company names from recruiting filenames

    ollama_base_url: str = "http://localhost:11434"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_extraction_model: str = "claude-haiku-4-5-20251001"
    use_anthropic_fallback: bool = True
    database_url: str = f"sqlite:///{Path.home() / '.deadline-agent' / 'tasks.db'}"
    log_level: str = "INFO"

    # Google OAuth
    google_credentials_path: str = str(Path.home() / ".deadline-agent" / "google_credentials.json")
    google_token_path: str = str(Path.home() / ".deadline-agent" / "google_token.json")

    # Gmail Pub/Sub
    gmail_pubsub_topic: str = ""  # e.g. "projects/my-project/topics/gmail-push"
    gmail_watch_renew_days: int = 6  # renew watch before 7-day expiry

    # Moodle
    moodle_ical_urls: list[str] = []
    moodle_fetch_interval_minutes: int = 60

    # Notifications
    enable_notifications: bool = True
    digest_time: str = "07:00"
    alert_check_interval_minutes: int = 15
    menubar_refresh_seconds: int = 60

    # Embedding dedup
    embedding_model: str = "nomic-embed-text"
    similarity_threshold: float = 0.85
    dedup_window_days: int = 7

    # Open WebUI
    openwebui_url: str = "http://localhost:3000"

    # Actions
    action_auto_execute: bool = False
    action_expiry_hours: int = 24

    # Reasoning
    extraction_model: str = "llama3.2:3b"  # Ollama model for task extraction
    reasoning_interval_minutes: int = 120
    reasoning_model: str = ""  # empty = use same model as extraction
    reasoning_provider: str = "ollama"  # "ollama" or "anthropic"

    # LoRA fine-tuning
    lora_log_training_data: bool = False  # opt-in: log extraction/digest I/O for training
    lora_extraction_model: str = ""  # fine-tuned extraction model tag, empty = use extraction_model
    lora_reasoning_model: str = ""  # fine-tuned reasoning model tag, empty = use reasoning_model

    # Identity model (Phase 17)
    identity_synthesis_enabled: bool = True
    identity_export_path: str = ""  # auto-export markdown to this path after each synthesis

    # Social graph
    enable_tone_analysis: bool = False
    enable_social_graph: bool = False

    # Widget
    widget_width: int = 380
    widget_height: int = 560
    widget_hotkey: str = "cmd+shift+d"

    # Behavioral memory
    session_gap_minutes: int = 30
    behavioral_analysis_interval_hours: int = 6
    min_pattern_confidence: float = 0.3

    # Life track classification
    recruiting_patterns: list[str] = []  # filename patterns, e.g. ["JudeElMasri*.pdf"]
    project_directories: list[str] = []

    # Phase 25: Recruiting intelligence
    company_tier_map: dict[str, str] = {}  # manual company→tier: {"google": "big_tech"}
    tier_inference_enabled: bool = True  # auto-classify unknown companies by heuristics
    recruiting_analytics_enabled: bool = True

    # Life contexts (manual season definitions)
    life_contexts: list[dict[str, str]] = []

    # Anticipatory triggers (Phase 13)
    enable_anticipatory_triggers: bool = True
    activity_drop_threshold_pct: float = 30.0
    deadline_cluster_count: int = 3
    deadline_cluster_days: int = 3
    peak_nudge_minutes_before: int = 20
    peak_nudge_check_interval_minutes: int = 10
    recruiting_fast_path_notify: bool = True

    # File watcher
    watch_directories: list[str] = []
    watch_extensions: list[str] = [
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".py",
        ".tex",
        ".md",
        ".zip",
        ".xlsx",
    ]
    watch_ignore_patterns: list[str] = [".*", "__pycache__", "node_modules", ".git"]
    no_work_alert_hours: int = 72
    file_link_threshold: float = 0.5
    enable_file_watcher: bool = True

    # Phase 16: Ambient presence
    enable_proactive_interrupts: bool = True
    enable_session_briefing: bool = True
    session_idle_minutes: int = 30
    enable_voice_input: bool = False
    whisper_model: str = "base.en"

    # Pre-meeting briefing
    enable_meeting_briefing: bool = True
    meeting_briefing_lead_minutes: int = 15
    meeting_briefing_check_interval_minutes: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _validate_reasoning_provider(self) -> "Settings":
        if self.reasoning_provider not in ("ollama", "anthropic"):
            raise ValueError(
                f"reasoning_provider must be 'ollama' or 'anthropic', "
                f"got '{self.reasoning_provider}'"
            )
        if self.reasoning_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("anthropic_api_key is required when reasoning_provider is 'anthropic'")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add TOML config as lowest-priority source (after env and .env)."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSource(settings_cls),
            file_secret_settings,
        )


settings = Settings()
