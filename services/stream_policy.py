"""
AnnounceFlow - Stream Policy
Priority rules for stream interaction with announcements, scheduled music,
and silence policy (prayer/working hours).

Responsibilities:
- Decide if stream should be interrupted (announcement incoming)
- Decide if scheduled music should be skipped (stream active)
- Decide if stream should be force-stopped (silence policy active)
- Decide if stream should resume (silence policy ended, sender alive)

Priority order (PI4_STREAM_V1_SCOPE.md section 3.1):
    Mesai/Ezan sessizlik > Anons > Stream > Arka plan loop/planli muzik

V1 scope: stateless policy functions, no DB, no config.
"""
import logging

logger = logging.getLogger(__name__)


def should_interrupt_for_announcement(stream_active: bool) -> bool:
    """Should the stream be paused for an incoming announcement?

    Args:
        stream_active: Whether a stream session is currently live.

    Returns:
        True if stream should pause for announcement.
    """
    # TODO(Faz 4): Implement announcement interrupt logic
    return stream_active


def should_skip_scheduled_music(stream_active: bool) -> bool:
    """Should a scheduled music trigger be skipped because stream is active?

    Args:
        stream_active: Whether a stream session is currently live.

    Returns:
        True if scheduled music should be skipped.
    """
    # TODO(Faz 4): Implement scheduled music skip logic
    return stream_active


def should_force_stop_stream(silence_active: bool) -> bool:
    """Should the stream be force-stopped due to silence policy?

    Args:
        silence_active: Whether prayer/working-hours silence is active.

    Returns:
        True if stream must be stopped immediately.
    """
    # TODO(Faz 4): Implement force-stop logic
    return silence_active


def should_resume_stream(silence_ended: bool, sender_alive: bool) -> bool:
    """Should the stream resume after silence policy ends?

    Args:
        silence_ended: Whether the silence period has just ended.
        sender_alive: Whether the agent sender is still connected/active.

    Returns:
        True if stream should resume.
    """
    # TODO(Faz 4): Implement resume logic
    return silence_ended and sender_alive
