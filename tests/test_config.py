"""Tests for TOML config loading and template generation."""

from pathlib import Path

from deadline_agent.config_loader import _expand_value, load_config_file
from deadline_agent.config_template import generate_template


class TestExpandValue:
    def test_expands_home(self) -> None:
        result = _expand_value("~/test")
        assert not result.startswith("~")
        assert "test" in result

    def test_expands_env_var(self, monkeypatch: object) -> None:
        import os

        os.environ["TEST_VAR"] = "expanded"
        result = _expand_value("$TEST_VAR/path")
        assert result == "expanded/path"
        del os.environ["TEST_VAR"]

    def test_non_string_unchanged(self) -> None:
        assert _expand_value(42) == 42
        assert _expand_value(True) is True


class TestLoadConfigFile:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = load_config_file(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_loads_flat_settings(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text(
            '[core]\nlog_level = "DEBUG"\n\n[ai]\nollama_base_url = "http://custom:1234"\n'
        )
        result = load_config_file(config)
        assert result["log_level"] == "DEBUG"
        assert result["ollama_base_url"] == "http://custom:1234"

    def test_maps_nested_keys(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('[notifications]\nenable = false\ndigest_time = "08:30"\n')
        result = load_config_file(config)
        assert result["enable_notifications"] is False
        assert result["digest_time"] == "08:30"

    def test_moodle_urls(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text('[moodle]\nical_urls = ["https://moodle.example.com/feed.ics"]\n')
        result = load_config_file(config)
        assert result["moodle_ical_urls"] == ["https://moodle.example.com/feed.ics"]

    def test_invalid_toml_returns_empty(self, tmp_path: Path) -> None:
        config = tmp_path / "config.toml"
        config.write_text("this is not valid toml {{{}}")
        result = load_config_file(config)
        assert result == {}


class TestGenerateTemplate:
    def test_is_valid_structure(self) -> None:
        template = generate_template()
        assert template.startswith("# Deadline Agent")
        # Verify section headers are present and properly formatted
        assert "[core]" in template
        assert "[ai]" in template

    def test_contains_all_sections(self) -> None:
        template = generate_template()
        for section in ["[core]", "[ai]", "[google]", "[moodle]", "[notifications]", "[dedup]"]:
            assert section in template
