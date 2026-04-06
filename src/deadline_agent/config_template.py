"""Generate a template config.toml with all defaults."""


def generate_template() -> str:
    """Produce a commented TOML template with all current defaults."""
    return """\
# Deadline Agent Configuration
# Place at ~/.deadline-agent/config.toml
# Environment variables override these values.

[core]
# log_level = "INFO"
# database_url = "sqlite:///~/.deadline-agent/tasks.db"

[ai]
# ollama_base_url = "http://localhost:11434"
# anthropic_api_key = ""  # or set ANTHROPIC_API_KEY env var
# anthropic_model = "claude-sonnet-4-6"  # used for reasoning (insights, digest, queries)
# anthropic_extraction_model = "claude-haiku-4-5-20251001"  # used for task extraction (cheaper)
# use_anthropic_fallback = true
# embedding_model = "nomic-embed-text"

[google]
# credentials_path = "~/.deadline-agent/google_credentials.json"
# token_path = "~/.deadline-agent/google_token.json"

[moodle]
# ical_urls = [
#     "https://moodle.example.edu/calendar/export_execute.php?..."
# ]
# fetch_interval_minutes = 60  # how often to poll Moodle iCal feeds

[notifications]
# enable = true
# digest_time = "07:00"
# alert_check_interval_minutes = 15

[dedup]
# similarity_threshold = 0.85
# window_days = 7

[ui]
# menubar_refresh_seconds = 60

[actions]
# auto_execute = false  # never auto-execute; require explicit approval
# expiry_hours = 24  # proposed actions expire after 24h

[reasoning]
# interval_minutes = 120  # how often to generate insights
# model = ""  # empty = use same model as extraction
# provider = "ollama"  # "ollama" or "anthropic" — use "anthropic" for higher quality reasoning

[life_contexts]
# contexts = [
#     { season = "recruiting", start = "2026-01-15", end = "2026-04-30", label = "Spring recruiting" },
#     { season = "exams", start = "2026-04-10", end = "2026-04-25" },
# ]

[watcher]
# directories = ["~/Documents", "~/Downloads"]
# extensions = [".pdf", ".docx", ".doc", ".pptx", ".py", ".tex", ".md", ".zip", ".xlsx"]
# ignore_patterns = [".*", "__pycache__", "node_modules", ".git"]
# no_work_alert_hours = 72
# file_link_threshold = 0.5
# enable = true
"""
