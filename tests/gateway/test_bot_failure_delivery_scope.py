"""Controls for the two native queue failure-delivery regressions."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.event import MessageEvent
from tests.gateway.test_bot_failure_delivery_edges import prepare
from tests.gateway.test_bot_failure_feedback import result, source


@pytest.mark.asyncio
async def test_generation_replaced_while_awaiting_stream_sends_nothing(monkeypatch, tmp_path):
    runner, adapter, event, pending, ctx, value = prepare(monkeypatch, tmp_path)
    current = [True]
    runner._is_session_run_current = lambda *_: current[0]
    entered, release = asyncio.Event(), asyncio.Event()
    async def await_stream(_):
        entered.set()
        await release.wait()
    runner._await_stream_task = AsyncMock(side_effect=await_stream)
    ctx.stream_consumer_holder = [object()]
    async def run(**kwargs):
        return await runner._run_agent_queued_followup(ctx, adapter, pending.text, pending, value, value, object())
    runner._run_agent = AsyncMock(side_effect=run)
    task = asyncio.create_task(runner._handle_message_with_agent(event, event.source, ctx.session_key, 1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        current[0] = False
        release.set()
        assert await asyncio.wait_for(task, timeout=10) is None
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    runner._await_stream_task.assert_awaited_once()
    adapter.sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_request_and_generation_can_notify_after_queue_error(monkeypatch, tmp_path):
    runner, adapter, event, pending, ctx, value = prepare(monkeypatch, tmp_path)
    async def queued(**kwargs):
        return await runner._run_agent_queued_followup(ctx, adapter, pending.text, pending, value, value, None)
    runner._run_agent = AsyncMock(side_effect=queued)
    runner._hmwa_persist_turn_transcript = AsyncMock(side_effect=OSError('synthetic disk failure'))
    await runner._handle_message_with_agent(event, event.source, ctx.session_key, 1)
    assert adapter.sent.await_count == 1
    runner._release_turn_lease(ctx.session_key, 1)  # outer inbound handler's normal finally
    for message_id, generation in [('777777777777777777', 1), (event.message_id, 2)]:
        next_event = MessageEvent(text='next failure', source=source(), message_id=message_id)
        runner._run_agent = AsyncMock(return_value=result(failed=True))
        await runner._handle_message_with_agent(next_event, next_event.source, ctx.session_key, generation)
        assert adapter.sent.await_args.kwargs['reply_to'] == message_id
        runner._release_turn_lease(ctx.session_key, generation)
    assert adapter.sent.await_count == 3
