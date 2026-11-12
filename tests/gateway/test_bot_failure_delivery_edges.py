"""Confirm reachable queued paths with native ledgered transport; expect correct contracts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.platforms.event import MessageEvent
from tests.gateway.test_bot_failure_feedback import setup, source, result
from tests.gateway.test_bot_failure_recovery import Transport


def prepare(monkeypatch, tmp_path):
    value = result(failed=True)
    runner, _ = setup(monkeypatch, tmp_path, value)
    runner.session_store.get_or_create_session.return_value.session_key = 'agent:main:discord:group:room'
    adapter = Transport(PlatformConfig(enabled=True), Platform.DISCORD)
    adapter.sent = AsyncMock(return_value=SendResult(success=True, message_id='999999999999999999'))
    runner._delivery_adapter_for = lambda s: adapter
    runner._MAX_INTERRUPT_DEPTH = 8
    runner._is_goal_continuation_event = lambda _: False
    runner._session_key_for_source = lambda _: 'agent:main:discord:group:room'
    native_prepare = runner._prepare_profile_scoped_inbound_message_text
    async def prepare_text(**kwargs):
        if kwargs['event'].message_id == '666666666666666666':
            return None
        return await native_prepare(**kwargs)
    runner._prepare_profile_scoped_inbound_message_text = AsyncMock(side_effect=prepare_text)
    event = MessageEvent(text='diagnose', source=source(), message_id='555555555555555555')
    pending = MessageEvent(text='@file:/etc/shadow please', source=source(), message_id='666666666666666666')
    ctx = SimpleNamespace(source=event.source, session_key='agent:main:discord:group:room', run_generation=1,
        stream_consumer_holder=[None], mute_notification_reply=False, inbound_message_id=event.message_id,
        session_id='s', _interrupt_depth=0, history=[], _status_thread_metadata=None,
        context_prompt=None, result_holder=[value], _cleanup_adapter=None, _cleanup_msg_ids=[],
        _cleanup_progress=False)
    return runner, adapter, event, pending, ctx, value

@pytest.mark.asyncio
async def test_duplicate_failure_through_real_queue_and_ledger(monkeypatch, tmp_path):
    from gateway import delivery_ledger as ledger
    monkeypatch.setattr(ledger, '_db_path', lambda: tmp_path/'ledger.db')
    monkeypatch.setattr(ledger, 'ledger_enabled', lambda: True)
    runner, adapter, event, pending, ctx, value = prepare(monkeypatch, tmp_path)
    async def run(**kwargs):
        return await runner._run_agent_queued_followup(ctx, adapter, pending.text, pending, value, value, None)
    runner._run_agent = AsyncMock(side_effect=run)
    runner._hmwa_persist_turn_transcript = AsyncMock(side_effect=OSError('synthetic disk failure'))
    assert await runner._handle_message_with_agent(event, event.source, ctx.session_key, 1) is None
    assert any(c.kwargs['event'] is pending for c in runner._prepare_profile_scoped_inbound_message_text.await_args_list)
    assert adapter.sent.await_count == 1, f'Native sends: {adapter.sent.await_args_list}'

@pytest.mark.asyncio
async def test_stale_generation_through_native_queue_and_outer_handler(monkeypatch, tmp_path):
    runner, adapter, event, pending, ctx, value = prepare(monkeypatch, tmp_path)
    current = [True]
    runner._is_session_run_current = lambda *_: current[0]
    async def run(**kwargs):
        # Worker completed just as a newer generation took over; queue drain still executes
        # inside _run_agent, before the outer handler can discard stale results.
        current[0] = False
        return await runner._run_agent_queued_followup(ctx, adapter, pending.text, pending, value, value, None)
    runner._run_agent = AsyncMock(side_effect=run)
    assert await runner._handle_message_with_agent(event, event.source, ctx.session_key, 1) is None
    adapter.sent.assert_not_awaited()
