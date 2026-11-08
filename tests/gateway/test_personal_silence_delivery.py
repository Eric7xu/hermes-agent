"""Successful silence is final delivery, never a license to hide incomplete work."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.response_filters import is_intentional_silence_agent_result
from tests.gateway.test_gateway_silence_tokens import _event, _runner


@pytest.mark.parametrize("changes", [
    {"failed": True}, {"partial": True}, {"interrupted": True},
    {"error": "provider unavailable"}, {"completed": False},
])
def test_incomplete_marker_is_not_successful_silence(changes):
    assert not is_intentional_silence_agent_result(changes, "NO_REPLY")


@pytest.mark.asyncio
@pytest.mark.parametrize("bot,already_sent", [(False, False), (True, False), (False, True), (True, True)])
async def test_silent_final_stops_before_voice_media_and_footer(monkeypatch, tmp_path, bot, already_sent):
    runner = _runner(monkeypatch, tmp_path)
    event = _event(reply_expected=True)
    event.source.is_bot = bot
    adapter = SimpleNamespace(send=AsyncMock())
    runner._delivery_adapter_for = Mock(return_value=adapter)
    runner._should_send_voice_reply = Mock(return_value=True)
    runner._send_voice_reply = AsyncMock()
    runner._deliver_media_from_response = AsyncMock()
    result = {"final_response": "NO_REPLY", "failed": False, "already_sent": already_sent}
    assert await runner._hmwa_deliver_turn_response(
        event, event.source, SimpleNamespace(session_id="silent"), "lane", 1,
        result, [], "NO_REPLY", "usage footer", True,
    ) == ""
    runner._should_send_voice_reply.assert_not_called()
    runner._send_voice_reply.assert_not_awaited()
    runner._deliver_media_from_response.assert_not_awaited()
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bot", [False, True])
@pytest.mark.parametrize("internal,expected", [(False, True), (False, False), (True, None)])
async def test_unhealthy_result_never_gets_silence_verdict(monkeypatch, tmp_path, bot, internal, expected):
    runner = _runner(monkeypatch, tmp_path)
    event = _event(internal=internal, reply_expected=expected)
    event.source.is_bot = bot
    entry = runner.session_store.get_or_create_session.return_value
    result = {"final_response": "NO_REPLY", "partial": True, "messages": []}
    response, silent, _ = await runner._hmwa_shape_agent_response(
        result, event.source, [], entry, "lane", "lane", 1, entry.session_id,
        "telegram", 0, "internal_notification" if internal else None, expected,
    )
    assert silent is False
    from gateway.response_filters import is_intentional_silence_response
    if bot:
        assert response == ""  # No runtime failure notice, and still not successful silence.
    else:
        assert response and not is_intentional_silence_response(response)


@pytest.mark.asyncio
async def test_partial_queued_marker_becomes_visible_failure(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    event = _event(reply_expected=False)
    ctx = SimpleNamespace(session_key="lane", source=event.source, stream_consumer_holder=[None],
                          mute_notification_reply=False, persist_user_display_kind="internal_notification",
                          reply_expected=False, _status_thread_metadata=None, event_message_id=None,
                          inbound_message_id="42", run_generation=1)
    runner._deliver_queued_first_response = AsyncMock(return_value=True)
    result = {"final_response": "NO_REPLY", "interrupted": True}
    await runner._run_agent_deliver_first_response(ctx, None, result, result, None)
    runner._deliver_queued_first_response.assert_awaited_once()
    from gateway.response_filters import is_intentional_silence_response
    delivered = runner._deliver_queued_first_response.await_args.args[0]
    assert delivered and not is_intentional_silence_response(delivered)
