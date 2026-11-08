"""Installed default silence adapted to v0.23 health, room and receipt contracts."""
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import GatewayConfig
from tests.gateway.test_gateway_silence_tokens import _event, _runner, _source
from tests.gateway.test_active_turn_recovery import _close_store_db, _db_runner, _entry_for


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["normal", "queued", "recovery"])
@pytest.mark.parametrize("is_bot", [False, True])
async def test_default_silence_is_durable_without_room_opt_in(monkeypatch, tmp_path, lane, is_bot):
    runner = _runner(monkeypatch, tmp_path)
    source = _source(); source.is_bot = is_bot
    event = _event(reply_expected=True); event.source = source
    result = {"final_response": "NO_REPLY", "failed": False, "messages": [
        {"role": "user", "content": "handoff", "display_metadata": {"reply_expected": True}},
        {"role": "assistant", "content": "NO_REPLY"}], "api_calls": 1}
    runner._run_agent = AsyncMock(return_value=result)
    runner._hmwa_runtime_footer_line = lambda *_: "usage footer"
    if lane == "queued":
        runner._deliver_queued_first_response = AsyncMock()
        ctx = NS(session_key="lane", source=source, stream_consumer_holder=[None],
                 mute_notification_reply=False, persist_user_display_kind=None, reply_expected=True,
                 _status_thread_metadata=None, event_message_id=None, inbound_message_id="42", run_generation=1)
        await runner._run_agent_deliver_first_response(ctx, None, result, result, None)
        runner._deliver_queued_first_response.assert_not_awaited()
    elif lane == "normal":
        assert await runner._handle_message_with_agent(event, source, "lane", 1) == ""
    else:
        from gateway.delivery_ledger import sweep_recoverable
        result["agent_persisted"] = False
        _, store = _db_runner(tmp_path); runner.session_store = store
        try:
            assert await runner._handle_message_with_agent(event, source, store._generate_session_key(source), 1) == ""
            entry = _entry_for(store, source)
            # A human default-silent turn must use the new durable receipt, not raw marker trust.
            if not is_bot:
                receipt = store.get_session_metadata(entry.session_key, "room_silence_decision")
                assert receipt["silent"] is True
            _close_store_db(store)
            recovered, store = _db_runner(tmp_path)
            assert await recovered._recover_unclean_sessions() == (0, 0)
            assert not sweep_recoverable(deliverable_platforms={"telegram"})
        finally:
            _close_store_db(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"failed": True}, {"partial": True}, {"interrupted": True},
                                      {"completed": False}, {"error": "provider error"}])
async def test_default_silence_does_not_hide_unhealthy_human_result(monkeypatch, tmp_path, changes):
    runner = _runner(monkeypatch, tmp_path)
    runner._run_agent = AsyncMock(return_value={"final_response": "NO_REPLY", "messages": [], **changes})
    response = await runner._handle_message_with_agent(_event(reply_expected=True), _source(), "lane", 1)
    from gateway.response_filters import is_intentional_silence_response
    assert response and not is_intentional_silence_response(response)


@pytest.mark.asyncio
async def test_explicit_room_false_still_overrides_personal_default(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    runner.config = GatewayConfig.from_dict({"platforms": {"telegram": {"channel_overrides": {
        "-1001": {"allow_silence": False}}}}})
    runner._run_agent = AsyncMock(return_value={"final_response": "NO_REPLY", "messages": []})
    assert await runner._handle_message_with_agent(_event(reply_expected=True), _source(), "lane", 1)


@pytest.mark.asyncio
async def test_routine_turnhold_deferral_logs_without_notification(monkeypatch, caplog):
    from gateway.run_turn_hygiene import GatewayTurnHygieneMixin
    runner = GatewayTurnHygieneMixin()
    runner._hmwa_hygiene_cancel_or_adopt = AsyncMock(return_value=None)
    runner._hmwa_hygiene_stamp = Mock()
    runner._hmwa_hygiene_notify = AsyncMock()
    cooldown = Mock()
    monkeypatch.setattr("gateway.run._record_hygiene_cooldown", cooldown)
    attempt = NS(commit_fence=NS(commit_watermark_fenced=False), agent=object(),
                 wait_started=time.monotonic(), meta={})
    class HoldExpired(Exception):
        pass
    with caplog.at_level("INFO"), pytest.raises(HoldExpired):
        try:
            raise HoldExpired()
        except HoldExpired:
            await runner._hmwa_hygiene_on_turn_hold(attempt, None, NS(session_id="sid"), "lane", _source())
    runner._hmwa_hygiene_notify.assert_not_awaited()
    cooldown.assert_called_once()
    assert "proceeding without compression" in caplog.text
