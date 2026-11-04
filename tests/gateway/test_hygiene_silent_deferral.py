"""Routine turn-hold deferral is log-only; real compression failures remain visible."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.run_turn_hygiene import GatewayTurnHygieneMixin


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_hygiene_deferral_does_not_emit_a_chat_notice(monkeypatch, failure):
    runner = GatewayTurnHygieneMixin()
    runner._hmwa_hygiene_cancel_or_adopt = AsyncMock(return_value=None)
    runner._hmwa_hygiene_stamp = Mock()
    runner._hmwa_hygiene_notify = AsyncMock()
    runner._hmwa_hygiene_record_failure_cooldown = AsyncMock()
    monkeypatch.setattr("gateway.run._record_hygiene_cooldown", Mock())
    fence = SimpleNamespace(commit_watermark_fenced=False, deadline_exceeded=False,
                            is_cancelled=False, progress_observed=True,
                            seconds_since_progress=lambda: 30)
    attempt = SimpleNamespace(commit_fence=fence, agent=Mock(), meta={}, wait_started=time.monotonic())
    entry = SimpleNamespace(session_id="test-hygiene")
    settings = SimpleNamespace(total_ceiling_seconds=60, timeout_seconds=30)
    handler = runner._hmwa_hygiene_on_timeout if failure else runner._hmwa_hygiene_on_turn_hold
    with pytest.raises(TimeoutError):
        try:
            raise TimeoutError("hold expired")
        except TimeoutError:
            await handler(attempt, settings, entry, "test-lane", Mock())
    assert runner._hmwa_hygiene_notify.await_count == int(failure)
    assert runner._hmwa_hygiene_record_failure_cooldown.await_count == int(failure)
