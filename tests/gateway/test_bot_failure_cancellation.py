"""Native cancellation provenance must not hide a real Bot failure."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.codex_runtime import run_codex_app_server_turn
from gateway.bot_failure_feedback import is_terminal_failure
from gateway.platforms.event import MessageEvent
from tests.agent.test_codex_app_server_transform import _codex_agent
from tests.gateway.test_bot_failure_feedback import setup, source


def codex_result(reason, *, error=None, calls_started=True, text=""):
    agent = _codex_agent(text)
    agent._interrupt_requested = True
    agent._interrupt_message = "stop or redirect"
    agent._tool_interrupt_reason = reason
    agent.clear_interrupt = Mock(side_effect=lambda: setattr(agent, "_tool_interrupt_reason", None))
    agent._codex_session.run_turn.return_value.interrupted = True
    agent._codex_session.run_turn.return_value.error = error
    if not calls_started:
        agent._codex_session.run_turn.side_effect = RuntimeError("transport unavailable")
    value = run_codex_app_server_turn(
        agent, user_message="task", original_user_message="task",
        messages=[{"role": "user", "content": "task"}], effective_task_id="task")
    agent.clear_interrupt.assert_called_once()
    return value


@pytest.mark.parametrize("reason", ["explicit stop requested", "user sent a new message"])
def test_codex_native_cancel_is_not_failure(reason):
    value = codex_result(reason)
    assert value["partial"] and value["interrupted"]
    assert not is_terminal_failure(value)


@pytest.mark.parametrize("reason,error,started", [
    ("explicit stop requested", "provider unavailable", True),
    ("execution timed out", None, True),
    ("turn lease lost", None, True),
    (None, None, True),
    ("explicit stop requested", None, False),
])
def test_cancel_flag_cannot_swallow_real_or_unknown_failure(reason, error, started):
    assert is_terminal_failure(codex_result(reason, error=error, calls_started=started))


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_native_cancel_never_alerts_but_partial_failure_retains_text(monkeypatch, tmp_path, queued):
    value = codex_result("user sent a new message")
    runner, adapter = setup(monkeypatch, tmp_path, value)
    event = MessageEvent(text="task", source=source(), message_id="555555555555555555")
    key = "agent:main:discord:group:room"
    if queued:
        ctx = SimpleNamespace(source=event.source, session_key=key, run_generation=1,
            stream_consumer_holder=[None], mute_notification_reply=False,
            inbound_message_id=event.message_id, persist_user_display_kind=None,
            reply_expected=True)
        await runner._run_agent_deliver_first_response(ctx, adapter, value, value, None)
    else:
        await runner._handle_message_with_agent(event, event.source, key, 1)
    adapter.send.assert_not_awaited()
    assert not value.get("_bot_failure_delivery")

    failed = codex_result("user sent a new message", error="provider unavailable", text="Useful partial answer")
    runner, adapter = setup(monkeypatch, tmp_path, failed)
    await runner._handle_message_with_agent(event, event.source, key, 2)
    adapter.send.assert_awaited_once()
    text = adapter.send.await_args.kwargs["content"]
    assert "Useful partial answer" in text
    assert "did not complete" in text.lower() or "not completed" in text.lower()


@pytest.mark.asyncio
async def test_queued_healthy_bot_ignores_human_required_room_and_records_silence(monkeypatch, tmp_path):
    value = {"final_response": "NO_REPLY", "completed": True, "api_calls": 1}
    runner, adapter = setup(monkeypatch, tmp_path, value)
    from unittest.mock import AsyncMock
    runner._record_room_silence_decision = AsyncMock()
    runner._deliver_queued_first_response = AsyncMock()
    ctx = SimpleNamespace(source=source(), session_key="lane", run_generation=1,
        stream_consumer_holder=[None], mute_notification_reply=False,
        inbound_message_id="555555555555555555", persist_user_display_kind=None, reply_expected=True)
    await runner._run_agent_deliver_first_response(ctx, adapter, value, value, None)
    assert runner._record_room_silence_decision.await_args.args[2] is True
    runner._deliver_queued_first_response.assert_not_awaited()
    adapter.send.assert_not_awaited()


@pytest.mark.parametrize("reason,failed", [("user sent a new message", False), ("gateway shutdown", True)])
def test_classic_finalizer_captures_reason_before_clearing(reason, failed):
    from tests.agent.test_turn_finalizer_interrupt_alternation import _StubAgent, _finalize, _interrupted_tool_tail
    agent = _StubAgent()
    agent._tool_interrupt_reason = reason
    agent.clear_interrupt = lambda: setattr(agent, "_tool_interrupt_reason", None)
    value = _finalize(agent, _interrupted_tool_tail(), interrupted=True)
    assert agent._tool_interrupt_reason is None
    assert value["interrupt_reason"] == reason
    assert is_terminal_failure(value) is failed


def test_partial_feedback_reserves_truthful_status_within_limit():
    from agent.turn_failure_copy import PARTIAL_FAILED_TURN_NOTICE
    from gateway.bot_failure_feedback import failure_text
    text = "Safe partial content " * 200
    value = {"partial": True, "final_response": text, "api_calls": 1}
    shaped = failure_text(value, source(), text)
    delivered = failure_text(value, source(), shaped)
    assert delivered.startswith("Safe partial content")
    assert delivered.endswith(PARTIAL_FAILED_TURN_NOTICE)
    assert delivered.count(PARTIAL_FAILED_TURN_NOTICE) == 1
    assert len(delivered) <= 1400
