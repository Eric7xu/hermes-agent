"""What the runtime itself may send to the author of an inbound message (#137027).

Routine runtime notices (busy acknowledgments, progress, status, compression and usage footers)
remain quiet for Bot peers. Terminal failure feedback has a separate native delivery contract in
``bot_failure_feedback``; useful model-authored replies and successful silence remain allowed.
Every routine-notice egress asks ``automatic_notices_allowed``; none re-derives the rule.
"""

from __future__ import annotations

from typing import Any


def automatic_notices_allowed(source: Any) -> bool:
    """False when the message came from a known bot (``SessionSource.is_bot``, a bool: the platform's
    bot flag or the adapter's ``bot_users`` opt-in). Anything but a real ``True`` is not a known bot."""
    return getattr(source, "is_bot", False) is not True
