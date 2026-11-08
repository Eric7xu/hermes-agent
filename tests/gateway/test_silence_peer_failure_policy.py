"""Runtime-written failure copy must not become a new turn for a bot peer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.bot_peer_notices import automatic_notices_allowed
from gateway.response_filters import is_intentional_silence_response, silence_allowed
from gateway.run_turn import GatewayTurnMixin
from gateway.session import Platform, SessionSource


class _Runner(GatewayTurnMixin):
    def __init__(self):
        self.async_session_store = SimpleNamespace(
            lookup_by_session_key=AsyncMock(return_value=SimpleNamespace(
                session_id="test", active_turn_token="current-token")),
            load_transcript=AsyncMock(return_value=[{
                "role": "assistant", "content": "NO_REPLY", "timestamp": 123.0}]),
            set_session_metadata=AsyncMock(),
        )

    def _silence_allowed_for_source(self, source, kind, expected, result):
        return silence_allowed(kind, expected, agent_result=result)


async def _shape(runner, source, result):
    return await runner._hmwa_shape_agent_response(
        result, source, [], SimpleNamespace(session_id="test"), None,
        None, 0, "test", "discord", 0, reply_expected=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("bot", [True, False])
@pytest.mark.parametrize("health", [
    {"partial": True}, {"interrupted": True}, {"error": "provider unavailable"},
    {"completed": False}, {"failed": True}, {},
])
async def test_marker_health_controls_both_delivery_lanes_and_receipts(bot, health):
    runner = _Runner()
    source = SessionSource(platform=Platform.DISCORD, chat_id="701", user_id="400", is_bot=bot)
    assert automatic_notices_allowed(source) is (not bot)
    result = {"final_response": "NO_REPLY", "api_calls": 1, "messages": [], **health}
    response, silent, _ = await _shape(runner, source, result)
    ctx = SimpleNamespace(source=source, persist_user_display_kind=None, reply_expected=True,
                          session_key="lane", stream_consumer_holder=[None], mute_notification_reply=False,
                          _status_thread_metadata=None, event_message_id=None, inbound_message_id="42",
                          run_generation=1)
    queued, queued_silent = runner._queued_silence_response(ctx, result, "NO_REPLY")
    assert silent is (not health) and queued_silent is (not health)
    if health:
        if bot:
            assert response == queued == ""
        else:
            assert response and queued
            assert not is_intentional_silence_response(response)
            assert not is_intentional_silence_response(queued)
    else:
        assert is_intentional_silence_response(response)
        assert queued == ""
    await runner._record_room_silence_decision("lane", result, silent, reply_expected=True)
    runner._deliver_queued_first_response = AsyncMock(return_value=True)
    await runner._run_agent_deliver_first_response(ctx, None, result, result, None)
    assert runner._deliver_queued_first_response.await_count == int(bool(health) and not bot)
    if health:
        runner.async_session_store.set_session_metadata.assert_not_awaited()
    else:
        assert runner.async_session_store.set_session_metadata.await_count == 2
        assert runner.async_session_store.set_session_metadata.await_args.args[2] == {
            "token": "current-token", "timestamp": 123.0, "silent": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("bot", [True, False])
@pytest.mark.parametrize("health", [{}, {"partial": True}, {"interrupted": True},
                                   {"error": "provider unavailable"}, {"completed": False}])
async def test_real_model_prose_is_not_runtime_failure_copy(bot, health):
    runner = _Runner()
    source = SessionSource(platform=Platform.DISCORD, chat_id="701", user_id="400", is_bot=bot)
    text = "The first calculation is complete; more work remains."
    result = {"final_response": text, "api_calls": 1, "messages": [], **health}
    response, silent, _ = await _shape(runner, source, result)
    ctx = SimpleNamespace(source=source, persist_user_display_kind=None, reply_expected=True, session_key="lane")
    assert response == text and silent is False
    assert runner._queued_silence_response(ctx, result, text) == (text, False)
