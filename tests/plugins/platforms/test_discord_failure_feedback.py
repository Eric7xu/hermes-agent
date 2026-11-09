"""Exercise Discord's real SDK wire policy, with only the network transport faked."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import discord

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter

OWNER = "111111111111111111"


@pytest.mark.asyncio
@pytest.mark.parametrize("owners,reference", [([OWNER], "555555555555555555"), ([], None), (["@everyone"], None)])
async def test_failure_send_has_explicit_safe_mentions_and_actual_reference(owners, reference):
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fake", reply_to_mode="off"))
    channel = SimpleNamespace(id=333333333333333333, send=AsyncMock(return_value=SimpleNamespace(id=9)))
    adapter._client = object()
    adapter._resolve_channel = AsyncMock(return_value=channel)
    adapter._is_forum_parent = lambda _: False
    adapter._record_response_async = AsyncMock(side_effect=lambda ref, res, *args: res)
    adapter.format_message = lambda text: text
    content = f"<@{OWNER}> failure @everyone <@&777777777777777777> <@888888888888888888>"
    sent = await adapter.send("222222222222222222", content, reply_to=reference,
                              metadata={"thread_id": str(channel.id), "failure_owner_ids": owners})
    assert sent.success
    options = channel.send.await_args.kwargs
    assert isinstance(options["allowed_mentions"], discord.AllowedMentions)
    assert options["allowed_mentions"].to_dict() == {"users": [int(OWNER)] if owners == [OWNER] else [], "parse": []}
    assert options["allowed_mentions"].replied_user is False
    if reference:
        assert options["reference"].message_id == int(reference)
        assert options["reference"].channel_id == channel.id
    else:
        assert options["reference"] is None
    adapter._resolve_channel.assert_awaited_once_with(str(channel.id))
