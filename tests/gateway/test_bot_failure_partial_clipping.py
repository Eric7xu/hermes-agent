"""Partial-failure status survives the final text budget in a single delivery pass."""
from types import SimpleNamespace

import pytest

from agent.turn_failure_copy import PARTIAL_FAILED_TURN_NOTICE as NOTICE
from gateway.bot_failure_feedback import failure_text
from tests.gateway.test_bot_failure_feedback import OWNER, setup, source


@pytest.mark.parametrize("text", [
    NOTICE + "\n\n" + "Useful partial answer. " * 80,
    "Useful partial answer. " * 30 + NOTICE + " More safe content." * 80,
    "Useful partial answer. " * 80 + "\n\n" + NOTICE,
    "Useful partial answer. " + "x" * (1380 - len("Useful partial answer. ")) + NOTICE,
], ids=["start", "middle", "end", "cross-budget"])
def test_existing_partial_notice_is_complete_and_idempotent(text):
    value = dict(partial=True, completed=False, final_response=text, api_calls=1)
    shaped = failure_text(value, source(), text)
    assert len(shaped) <= 1400
    assert shaped.count(NOTICE) == 1
    assert shaped.endswith(NOTICE)
    assert "Useful partial answer." in shaped
    assert failure_text(value, source(), shaped) == shaped


@pytest.mark.asyncio
async def test_queued_partial_feedback_keeps_existing_notice_after_clipping(monkeypatch, tmp_path):
    text = "Useful partial answer. " * 80 + "\n\n" + NOTICE
    value = dict(partial=True, completed=False, final_response=text, api_calls=1)
    runner, adapter = setup(monkeypatch, tmp_path, value)
    ctx = SimpleNamespace(source=source(), session_key="agent:main:discord:group:room",
        run_generation=1, stream_consumer_holder=[None], mute_notification_reply=False,
        inbound_message_id="555555555555555555")
    await runner._run_agent_deliver_first_response(ctx, adapter, value, value, None)
    adapter.send.assert_awaited_once()
    sent = adapter.send.await_args.kwargs
    assert sent["reply_to"] == ctx.inbound_message_id
    assert sent["metadata"]["failure_owner_ids"] == [OWNER]
    body = sent["content"].removeprefix(f"<@{OWNER}>\n")
    assert "Useful partial answer." in body
    assert body.count(NOTICE) == 1 and body.endswith(NOTICE)
    assert len(body) <= 1400
