"""Opted-in guild handoffs use transport authorship on live and recovery ingress."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from tests.gateway.test_discord_free_response import (
    FakeTextChannel, FakeThread, adapter as discord_adapter_fixture, make_message,
)

adapter = discord_adapter_fixture


@pytest.fixture
def guild_adapter(adapter):
    adapter.config.extra.update({
        "bot_collaboration_guilds": ["100"], "allow_bots": "mentions",
        "auto_thread": False, "history_backfill": False,
    })
    adapter._client.user.bot = True
    adapter._get_allowed_channels = lambda: {"1"}  # A legacy room list must not fence new posts.
    adapter._ready_event.set()
    return adapter


def _handoff_message(adapter, *, address="reply", thread=False):
    channel = FakeThread(channel_id=700, parent=FakeTextChannel(channel_id=70)) if thread else FakeTextChannel(channel_id=701)
    channel.guild.id = 100
    msg = make_message(channel=channel, content="handoff", mentions=[])
    msg.guild = channel.guild
    msg.author.bot = True
    if address == "mention":
        msg.content = f"<@{adapter._client.user.id}> handoff"
        msg.mentions = [adapter._client.user]
    elif address == "reply":
        msg.type = discord.MessageType.reply
        target = SimpleNamespace(id=120, author=adapter._client.user, guild=msg.guild,
                                 channel=channel, attachments=[])
        msg.reference = SimpleNamespace(message_id=120, guild_id=100, channel_id=channel.id,
                                        resolved=target, cached_message=None)
    return msg


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered,address,thread", [
    (False, "mention", True), (False, "reply", False),
    (True, "reply", True), (True, "mention", False),
])
async def test_addressed_bot_reaches_native_dispatch(guild_adapter, recovered, address, thread):
    msg = _handoff_message(guild_adapter, address=address, thread=thread)
    dispatch = guild_adapter._dispatch_recovered_message if recovered else guild_adapter._dispatch_discord_message
    assert await dispatch(msg) is True
    guild_adapter.handle_message.assert_awaited_once()
    event = guild_adapter.handle_message.await_args.args[0]
    assert event.source.is_bot is True
    assert event.source.guild_id == "100"
    assert event.source.chat_id == str(msg.channel.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    "foreign-guild", "dm", "disabled", "none", "unaddressed", "human", "self",
    "wrong-author", "wrong-target-id", "wrong-reference-channel", "wrong-target-guild",
    "ignored", "duplicate",
])
async def test_guild_adapter_never_bypasses_negative_controls(guild_adapter, invalid):
    msg = _handoff_message(guild_adapter)
    mutations = {
        "foreign-guild": lambda: setattr(msg.guild, "id", 101),
        "dm": lambda: setattr(msg, "guild", None),
        "disabled": lambda: guild_adapter.config.extra.pop("bot_collaboration_guilds"),
        "none": lambda: guild_adapter.config.extra.update(allow_bots="none"),
        "self": lambda: setattr(msg, "author", guild_adapter._client.user),
        "wrong-author": lambda: setattr(msg.reference.resolved, "author", SimpleNamespace(id=400)),
        "wrong-target-id": lambda: setattr(msg.reference.resolved, "id", 121),
        "wrong-reference-channel": lambda: setattr(msg.reference, "channel_id", 702),
        "wrong-target-guild": lambda: setattr(msg.reference.resolved, "guild", SimpleNamespace(id=101)),
        "ignored": lambda: setattr(guild_adapter, "_get_ignored_channels", lambda: {str(msg.channel.id)}),
        "duplicate": lambda: guild_adapter._dedup.is_duplicate(str(msg.id)),
    }
    if invalid == "unaddressed":
        msg.reference = None
        guild_adapter.config.extra["allow_bots"] = "all"
        guild_adapter._text_batch_delay_seconds = 0.6
        guild_adapter._bot_tag_debounce_until[guild_adapter._bot_tag_debounce_key(msg)] = float("inf")
    elif invalid == "human":
        msg.author.bot = False
        guild_adapter._is_allowed_user = lambda *a, **kw: False
    else:
        mutations[invalid]()
    assert await guild_adapter._dispatch_discord_message(msg) is False
    guild_adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch_result", ["own", "other", "missing"])
async def test_uncached_reply_fetches_only_transport_target(guild_adapter, fetch_result):
    msg = _handoff_message(guild_adapter)
    target = msg.reference.resolved
    msg.reference.resolved = None
    if fetch_result == "other":
        target.author = SimpleNamespace(id=400)
    msg.channel.fetch_message = AsyncMock(return_value=target)
    if fetch_result == "missing":
        msg.channel.fetch_message.side_effect = OSError("unavailable")
    assert await guild_adapter._dispatch_discord_message(msg) is (fetch_result == "own")
    msg.channel.fetch_message.assert_awaited_once_with(120)
    assert guild_adapter.handle_message.await_count == (1 if fetch_result == "own" else 0)
