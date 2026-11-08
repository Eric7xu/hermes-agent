"""Actual installed collaboration policy, exercised through Discord ingress."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter
from tests.gateway.test_discord_missed_message_backfill import FakeChannel, make_bot_message


def setup_adapter():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fake-token", extra={
        "bot_collaboration_guilds": ["777"], "allow_bots": "mentions",
        "allowed_channels": ["old-room"], "auto_thread": False,
    }))
    adapter._client = NS(user=NS(id=999, bot=True, display_name="Receiver", name="receiver"))
    adapter._ready_event.set()
    adapter.handle_message = AsyncMock()
    adapter._enqueue_text_event = Mock()
    return adapter


def message(adapter, *, channel_id=123, reply=False, content=None):
    msg = make_bot_message(channel=FakeChannel(channel_id),
                           content=content if content is not None else "<@999> collaborate",
                           mentions=[adapter._client.user] if not reply else [])
    if reply:
        msg.type = discord.MessageType.reply
        msg.reference = NS(message_id=88, guild_id=777, channel_id=channel_id,
                           resolved=NS(id=88, author=adapter._client.user,
                                       guild=msg.guild, channel=msg.channel), cached_message=None)
    return msg


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["live", "recovered"])
@pytest.mark.parametrize("channel_id", [123, 987654])
@pytest.mark.parametrize("reply", [False, True])
async def test_addressed_collaborator_reaches_current_and_future_rooms(lane, channel_id, reply):
    adapter = setup_adapter()
    msg = message(adapter, channel_id=channel_id, reply=reply,
                  content="handoff" if reply else None)
    dispatch = adapter._dispatch_discord_message if lane == "live" else adapter._dispatch_recovered_message
    assert await dispatch(msg) is True
    assert adapter._enqueue_text_event.called or adapter.handle_message.called


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["none", "ignored", "wrong-guild", "unaddressed", "human-denied", "self", "duplicate"])
async def test_collaboration_never_bypasses_hard_gates(gate):
    adapter = setup_adapter()
    msg = message(adapter)
    def deny_human():
        msg.author.bot = False
        adapter._is_allowed_user = Mock(return_value=False)
        adapter._warn_if_fail_closed_default = Mock()

    def unaddressed():
        msg.content = "side chatter"
        msg.mentions = []
        adapter._is_bot_tag_debounce_continuation = Mock(return_value=True)

    mutations = {
        "none": lambda: adapter.config.extra.update(allow_bots="none"),
        "ignored": lambda: adapter.config.extra.update(ignored_channels=["123"]),
        "wrong-guild": lambda: setattr(msg, "guild", NS(id=778)),
        "unaddressed": unaddressed,
        "human-denied": deny_human,
        "self": lambda: setattr(msg, "author", adapter._client.user),
        "duplicate": lambda: adapter._dedup.is_duplicate(str(msg.id)),
    }
    mutations[gate]()
    assert await adapter._dispatch_discord_message(msg) is False
    adapter._enqueue_text_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [None, "author", "id", "guild", "channel", "reference-channel", "missing"])
async def test_reply_requires_verified_same_channel_own_target(mismatch):
    adapter = setup_adapter()
    msg = message(adapter, reply=True, content="handoff")
    target = msg.reference.resolved
    mutations = {
        "author": (target, "author", NS(id=111)),
        "id": (target, "id", 89),
        "guild": (target, "guild", NS(id=778)),
        "channel": (target, "channel", NS(id=124)),
        "reference-channel": (msg.reference, "channel_id", 124),
    }
    if mismatch in mutations:
        setattr(*mutations[mismatch])
    if mismatch == "missing":
        target = None
    msg.reference.resolved = None
    msg.channel.fetch_message = AsyncMock(return_value=target)
    assert await adapter._dispatch_discord_message(msg) is (mismatch is None)
    if mismatch == "reference-channel": msg.channel.fetch_message.assert_not_called()
    else: msg.channel.fetch_message.assert_awaited_once_with(88)


@pytest.mark.parametrize("mismatch", [None, "guild", "channel", "author", "extra-field", "bare-id"])
def test_legacy_allowlist_is_an_exact_tuple_not_a_wildcard(mismatch):
    adapter = setup_adapter()
    adapter.config.extra = {"allow_bots": "mentions", "bot_reply_allowlist": [
        {"guild_id": "777", "channel_id": "123", "author_id": "42"}]}
    msg = message(adapter, reply=True, content="handoff")
    rule = adapter.config.extra["bot_reply_allowlist"][0]
    if mismatch in ("guild", "channel", "author"): rule[mismatch + "_id"] = "987"
    elif mismatch == "extra-field": rule["unexpected"] = True
    elif mismatch == "bare-id": msg.reference.resolved = None
    assert adapter._discord_message_admission(msg, claim=False)[0] is (mismatch is None)
