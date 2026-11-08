"""Exact legacy reply exceptions remain narrower than guild collaboration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from plugins.platforms.discord.bot_collaboration import addressed_collaborator
from tests.plugins.platforms.test_discord_bot_collaboration import (
    adapter as discord_adapter_fixture, guild_adapter as guild_adapter_fixture, _handoff_message,
)

adapter = discord_adapter_fixture
guild_adapter = guild_adapter_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "guild", "channel", "author", "target", "none", "ignored"])
@pytest.mark.parametrize("recovered", [False, True])
async def test_exact_reply_rule_admits_only_its_transport_scope(guild_adapter, invalid, recovered):
    msg = _handoff_message(guild_adapter)
    guild_adapter.config.extra.pop("bot_collaboration_guilds")
    guild_adapter.config.extra["bot_reply_allowlist"] = [{
        "guild_id": "100", "channel_id": str(msg.channel.id), "author_id": str(msg.author.id),
    }]
    guild_adapter._get_allowed_channels = lambda: {str(msg.channel.id)}
    mutations = {
        "target": lambda: setattr(msg.reference.resolved, "author", SimpleNamespace(id=400)),
        "none": lambda: guild_adapter.config.extra.update(allow_bots="none"),
        "ignored": lambda: setattr(guild_adapter, "_get_ignored_channels", lambda: {str(msg.channel.id)}),
    }
    if invalid in {"guild", "channel", "author"}:
        guild_adapter.config.extra["bot_reply_allowlist"][0][f"{invalid}_id"] = "999"
    elif invalid in mutations:
        mutations[invalid]()
    dispatch = guild_adapter._dispatch_recovered_message if recovered else guild_adapter._dispatch_discord_message
    assert await dispatch(msg) is (invalid is None)
    assert guild_adapter.handle_message.await_count == int(invalid is None)


@pytest.mark.asyncio
async def test_exact_rule_never_bypasses_channel_allowlist(guild_adapter):
    msg = _handoff_message(guild_adapter)
    guild_adapter.config.extra.pop("bot_collaboration_guilds")
    guild_adapter.config.extra["bot_reply_allowlist"] = [{
        "guild_id": "100", "channel_id": str(msg.channel.id), "author_id": str(msg.author.id),
    }]
    assert await guild_adapter._dispatch_discord_message(msg) is False
    guild_adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("failure", ["deleted", "forbidden", "timeout"])
async def test_uncached_reply_errors_fail_closed(guild_adapter, recovered, failure):
    msg = _handoff_message(guild_adapter)
    msg.reference.resolved = None
    response = SimpleNamespace(status=404 if failure == "deleted" else 403, reason=failure)
    error = TimeoutError() if failure == "timeout" else (
        discord.NotFound(response, "deleted") if failure == "deleted" else discord.Forbidden(response, "forbidden"))
    msg.channel.fetch_message = AsyncMock(side_effect=error)
    dispatch = guild_adapter._dispatch_recovered_message if recovered else guild_adapter._dispatch_discord_message
    assert await dispatch(msg) is False
    msg.channel.fetch_message.assert_awaited_once_with(120)
    guild_adapter.handle_message.assert_not_awaited()


@pytest.mark.parametrize("scope", ["missing", "foreign", "dm"])
def test_channel_metadata_cannot_grant_guild_collaboration(guild_adapter, scope):
    msg = _handoff_message(guild_adapter, address="mention")
    if scope == "missing":
        msg.guild = None  # channel.guild still claims the allowed guild
    elif scope == "foreign":
        msg.guild = SimpleNamespace(id=999)
    else:
        msg.channel = MagicMock(spec=discord.DMChannel)
        msg.channel.id = 701  # even contradictory guild metadata cannot turn a DM into a guild event
    assert addressed_collaborator(guild_adapter, msg) is False


@pytest.mark.asyncio
async def test_exact_uncached_reply_uses_same_channel_lookup(guild_adapter):
    msg = _handoff_message(guild_adapter)
    guild_adapter.config.extra.pop("bot_collaboration_guilds")
    guild_adapter.config.extra["bot_reply_allowlist"] = [{
        "guild_id": "100", "channel_id": str(msg.channel.id), "author_id": str(msg.author.id),
    }]
    guild_adapter._get_allowed_channels = lambda: {str(msg.channel.id)}
    msg.channel.fetch_message = AsyncMock(return_value=msg.reference.resolved)
    msg.reference.resolved = None
    assert await guild_adapter._dispatch_recovered_message(msg) is True
    msg.channel.fetch_message.assert_awaited_once_with(120)
    guild_adapter.handle_message.assert_awaited_once()
