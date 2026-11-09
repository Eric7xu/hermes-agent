"""Failure delivery keeps its reference and mention policy through native ledger recovery."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent
from gateway.run_startup import GatewayStartupMixin
from tests.gateway.test_bot_failure_feedback import OWNER, source


class Transport(BasePlatformAdapter):
    async def get_chat_info(self, chat_id):
        return {"name": "test", "type": "group"}

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return await self.sent(chat_id=chat_id, content=content, reply_to=reply_to, metadata=metadata)


@pytest.mark.asyncio
async def test_failure_ledger_roundtrip_keeps_owner_reference_and_transport(monkeypatch, tmp_path):
    from gateway import delivery_ledger as ledger
    monkeypatch.setattr(ledger, "_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(ledger, "_owner_stamp", lambda: (777, 123))
    monkeypatch.setattr(ledger, "_owner_alive", lambda *_: False)
    adapter = Transport(PlatformConfig(enabled=True), Platform.DISCORD)
    adapter._owner_profile = "satellite"
    adapter.sent = AsyncMock(return_value=SendResult(success=False, error="blocked"))
    event = MessageEvent(text="", source=source(profile="satellite"), message_id="555555555555555555")
    event._failure_delivery = {"owner_ids": [OWNER], "reply_to": event.message_id}
    metadata = {"thread_id": event.source.thread_id, "failure_owner_ids": [OWNER]}
    content = f"<@{OWNER}> failure"
    result, transport = await adapter.send_final_ledgered(event, "agent:satellite:discord:room", content,
                                                         metadata, reply_to=event.message_id)
    assert not result.success and transport is adapter
    rows = ledger.sweep_recoverable(deliverable_targets={("discord", "satellite")})
    assert len(rows) == 1
    assert rows[0]["profile"] == "satellite"
    runner = object.__new__(GatewayStartupMixin)
    runner._obligation_adapter = AsyncMock(return_value=adapter)
    runner._arm_flood_timers_for_waiting_rows = AsyncMock()
    adapter.sent.return_value = SimpleNamespace(success=True)
    assert await runner._redeliver_claimed_obligations(rows) == 1
    replay = adapter.sent.await_args.kwargs
    assert replay["reply_to"] == event.message_id
    assert replay["metadata"] == metadata
    assert replay["chat_id"] == event.source.chat_id
    assert ledger.sweep_recoverable() == []
