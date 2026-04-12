"""On-device speech-to-text using OpenAI Whisper (local model)."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _ensure_ffmpeg_on_path() -> None:
    """Add common Homebrew/system paths so Whisper can find ffmpeg."""
    extra_dirs = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
    current = os.environ.get("PATH", "")
    missing = [d for d in extra_dirs if d not in current]
    if missing:
        os.environ["PATH"] = ":".join(missing) + ":" + current


class WhisperTranscriber:
    """Transcribe audio files using a local Whisper model.

    The model is loaded lazily on first use to avoid blocking startup.
    All processing happens on-device — audio never leaves the machine.
    """

    def __init__(self, model_name: str = "base.en") -> None:
        self._model_name = model_name
        self._model = None

    def load_model(self) -> None:
        """Load the Whisper model. Called lazily on first transcription."""
        _ensure_ffmpeg_on_path()
        try:
            import whisper

            logger.info("Loading Whisper model: %s", self._model_name)
            self._model = whisper.load_model(self._model_name)
            logger.info("Whisper model loaded")
        except ImportError:
            raise ImportError(
                "whisper is not installed. Install with: "
                "pip install openai-whisper"
            )

    def transcribe(self, audio_path: str) -> str:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to a WAV/MP3/etc. audio file.

        Returns:
            Transcribed text string.
        """
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if self._model is None:
            self.load_model()

        _ensure_ffmpeg_on_path()
        result = self._model.transcribe(audio_path, fp16=False)
        text = result.get("text", "").strip()
        logger.info("Transcribed %d chars from %s", len(text), audio_path)
        return text
