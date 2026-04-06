"""macOS native notification delivery via osascript."""

import logging
import platform
import subprocess

logger = logging.getLogger(__name__)


def _escape_applescript(text: str) -> str:
    """Escape special characters for AppleScript string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def send_notification(
    title: str,
    body: str,
    subtitle: str = "",
    sound: bool = True,
) -> bool:
    """Send a macOS notification using osascript.

    Returns True if the notification was sent successfully.
    Falls back to logging on non-macOS platforms.
    """
    if platform.system() != "Darwin":
        logger.warning("Notifications only supported on macOS. Skipping: %s", title)
        return False

    title_esc = _escape_applescript(title)
    body_esc = _escape_applescript(body)
    subtitle_esc = _escape_applescript(subtitle)

    script_parts = [f'display notification "{body_esc}" with title "{title_esc}"']
    if subtitle:
        script_parts[0] += f' subtitle "{subtitle_esc}"'
    if sound:
        script_parts[0] += ' sound name "default"'

    try:
        subprocess.run(
            ["osascript", "-e", script_parts[0]],
            capture_output=True,
            timeout=5,
            check=True,
        )
        logger.debug("Notification sent: %s", title)
        return True
    except FileNotFoundError:
        logger.error("osascript not found — are you on macOS?")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Notification timed out: %s", title)
        return False
    except subprocess.CalledProcessError as e:
        logger.error("Notification failed: %s — %s", title, e.stderr)
        return False
