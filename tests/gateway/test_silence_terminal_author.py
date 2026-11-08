"""Final response notices belong to the queued terminal author, not the opener."""
import pytest

from gateway.response_filters import is_intentional_silence_response
from gateway.session import Platform, SessionSource
from tests.gateway.test_silence_peer_failure_policy import _Runner, _shape


def _queued_result(opener_bot, terminal_bot, text, health):
    opener = SessionSource(platform=Platform.DISCORD, chat_id="701", user_id="opener", is_bot=opener_bot)
    terminal = SessionSource(platform=Platform.DISCORD, chat_id="701", user_id="terminal", is_bot=terminal_bot)
    result = {"final_response": text, "api_calls": 1, "messages": [],
              "queued_terminal_source": terminal, "queued_terminal_display_kind": None,
              "queued_terminal_reply_expected": True, **health}
    return opener, result


@pytest.mark.asyncio
@pytest.mark.parametrize("opener_bot,terminal_bot", [(False, True), (True, False), (True, True), (False, False)])
@pytest.mark.parametrize("health", [
    {"partial": True}, {"interrupted": True}, {"error": "provider unavailable"},
    {"completed": False}, {"failed": True}, {},
])
async def test_terminal_author_owns_marker_failure_and_success_receipt(opener_bot, terminal_bot, health):
    runner = _Runner()
    opener, result = _queued_result(opener_bot, terminal_bot, "NO_REPLY", health)
    response, silent, _ = await _shape(runner, opener, result)
    await runner._record_room_silence_decision("lane", result, silent, reply_expected=True)
    assert silent is (not health)
    store = runner.async_session_store
    if health:
        assert bool(response) is (not terminal_bot)
        assert not is_intentional_silence_response(response)
        store.set_session_metadata.assert_not_awaited()
    else:
        assert is_intentional_silence_response(response)
        store.set_session_metadata.assert_awaited_once_with("lane", "room_silence_decision", {
            "token": "current-token", "timestamp": 123.0, "silent": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("opener_bot,terminal_bot", [(False, True), (True, False)])
@pytest.mark.parametrize("text,health", [
    ("", {"failed": True}), ("(empty)", {}), ("", {"partial": True}),
])
async def test_terminal_author_owns_downstream_runtime_copy(opener_bot, terminal_bot, text, health):
    runner = _Runner()
    opener, result = _queued_result(opener_bot, terminal_bot, text, health)
    response, silent, _ = await _shape(runner, opener, result)
    assert silent is False
    assert bool(response) is (not terminal_bot)


@pytest.mark.asyncio
@pytest.mark.parametrize("opener_bot,terminal_bot", [(False, True), (True, False)])
@pytest.mark.parametrize("health", [{}, {"partial": True}, {"interrupted": True},
                                   {"error": "provider unavailable"}, {"completed": False}])
async def test_terminal_ownership_does_not_swallow_model_prose(opener_bot, terminal_bot, health):
    runner = _Runner()
    text = "The first calculation is complete; more work remains."
    opener, result = _queued_result(opener_bot, terminal_bot, text, health)
    response, silent, _ = await _shape(runner, opener, result)
    assert response == text and silent is False
