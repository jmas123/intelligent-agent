"""Audio recording from the default input device via sounddevice."""

import logging
import tempfile
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

TEMP_DIR = Path(tempfile.gettempdir()) / "deadline-agent"


class AudioRecorder:
    """Record audio from the default microphone.

    Records to a temporary WAV file that should be deleted after transcription.
    """

    def __init__(
        self, sample_rate: int = 16000, channels: int = 1
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._recording = False
        self._frames: list = []
        self._stream = None

    def start_recording(self) -> None:
        """Start recording from the default input device."""
        try:
            import sounddevice as sd
        except ImportError:
            raise ImportError(
                "sounddevice is not installed. Install with: "
                "pip install sounddevice"
            )

        if self._recording:
            logger.warning("Already recording")
            return

        self._frames = []
        self._recording = True

        def callback(indata, frames, time_info, status):
            if status:
                logger.warning("Audio input status: %s", status)
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()
        logger.info("Recording started")

    def stop_recording(self) -> str:
        """Stop recording and save to a temporary WAV file.

        Returns:
            Path to the temporary WAV file.
        """
        if not self._recording or self._stream is None:
            raise RuntimeError("Not currently recording")

        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._recording = False

        # Save to WAV
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        wav_path = TEMP_DIR / "recording.wav"

        import numpy as np

        audio_data = np.concatenate(self._frames, axis=0)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio_data.tobytes())

        self._frames = []
        logger.info("Recording saved: %s", wav_path)
        return str(wav_path)

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._recording

    def cleanup(self, path: str) -> None:
        """Delete a temporary recording file."""
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to clean up: %s", path)
